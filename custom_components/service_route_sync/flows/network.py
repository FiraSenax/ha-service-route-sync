from uuid import uuid4
import asyncio
import voluptuous as vol
from homeassistant.helpers import selector
from ..backends.unifi import UniFiBackend, read_controller
from ..backends.gateway_ssh import GatewaySSHConfig, GatewayTransportError, read_gateway
from ..gateway_credentials import (
    credential_directory,
    prepare_observer_key,
    save_known_host,
    validate_host_key,
    bind_existing_gateway_host,
)
from ..network.gateway_observation import parse_gateway_observation
from ..network.network import NetworkDataError
from ..backends.client_probe_ssh import ClientProbeSSHConfig
from homeassistant.util import dt as dt_util


class NetworkSteps:
    async def async_step_network(self, user_input=None):
        """Select a gateway explicitly; enabling this feature performs only reads."""
        if not self.settings.get("network_enabled"):
            self.settings["gateway_observer_enabled"] = False
            self.settings["ipv6_actor_enabled"] = False
            self.settings["ipv6_actor_active"] = False
            return await self.async_step_clients()
        try:
            gateways = await read_controller(
                self.hass, self.settings, "read_gateways", backend_factory=UniFiBackend
            )
        except Exception:
            return self.async_abort(reason="router_unavailable")
        if not gateways:
            return self.async_abort(reason="no_gateways")
        choices = [
            {"value": key, "label": str(row.get("name") or row.get("model") or key)}
            for key, row in gateways.items()
        ]
        selected = self.settings.get("network_gateway_id")
        marker = (
            vol.Required("network_gateway_id", default=selected)
            if selected in gateways
            else vol.Required("network_gateway_id")
        )
        schema = {
            marker: selector.SelectSelector(selector.SelectSelectorConfig(options=choices)),
            vol.Required(
                "network_primary_label", default=self.settings["network_primary_label"]
            ): vol.All(str, vol.Coerce(str.strip), vol.Length(min=1, max=40)),
            vol.Required(
                "network_secondary_label", default=self.settings["network_secondary_label"]
            ): vol.All(str, vol.Coerce(str.strip), vol.Length(min=1, max=40)),
            vol.Required(
                "network_interval_seconds", default=self.settings["network_interval_seconds"]
            ): vol.All(int, vol.Range(min=5, max=60)),
            vol.Optional(
                "gateway_observer_enabled",
                default=self.settings.get("gateway_observer_enabled", False),
            ): selector.BooleanSelector(),
            vol.Optional("gateway_observer_reconfigure", default=False): selector.BooleanSelector(),
            vol.Optional(
                "ipv6_actor_enabled", default=self.settings.get("ipv6_actor_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional(
                "ipv6_actor_active", default=self.settings.get("ipv6_actor_active", False)
            ): selector.BooleanSelector(),
            vol.Optional("ipv6_actor_reconfigure", default=False): selector.BooleanSelector(),
        }
        errors = {}
        if user_input is not None:
            try:
                user_input = vol.Schema(schema)(user_input)
                if user_input["network_gateway_id"] not in gateways:
                    raise vol.Invalid("Unknown gateway")
                reconfigure = user_input.pop("gateway_observer_reconfigure", False)
                actor_reconfigure = user_input.pop("ipv6_actor_reconfigure", False)
                if user_input.get("ipv6_actor_enabled") and not user_input.get(
                    "gateway_observer_enabled"
                ):
                    raise vol.Invalid("IPv6 actor requires gateway observer")
                if user_input.get("ipv6_actor_active") and not user_input.get("ipv6_actor_enabled"):
                    raise vol.Invalid("Inactive IPv6 actor cannot write")
                self.settings.update(user_input)
                if not self.settings.get("ipv6_actor_enabled"):
                    self.settings["ipv6_actor_active"] = False
                if actor_reconfigure:
                    self.settings.pop("ipv6_actor_key_id", None)
                if self.settings.get("gateway_observer_enabled"):
                    host = gateways[self.settings["network_gateway_id"]].get("ip")
                    try:
                        GatewaySSHConfig(host, credential_directory(self.hass, "0" * 32))
                    except (ValueError, GatewayTransportError):
                        return self.async_abort(reason="observer_address_unavailable")
                    if reconfigure or not self.settings.get("gateway_observer_key_id"):
                        # A canceled wizard must never overwrite active trust material.
                        self.settings["gateway_observer_host"] = host
                        self.settings["gateway_observer_key_id"] = uuid4().hex
                        return await self.async_step_gateway_observer()
                return await self._after_gateway_observer()
            except vol.Invalid:
                errors["base"] = "invalid_gateway"
        return self.async_show_form(
            step_id="network", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_gateway_observer(self, user_input=None):
        """Explicit public-key pinning and a real read-only protocol handshake."""
        errors = {}
        try:
            public_key = await prepare_observer_key(
                self.hass, self.settings["gateway_observer_key_id"]
            )
        except (OSError, GatewayTransportError, UnicodeError, ValueError):
            return self.async_abort(reason="observer_key_unavailable")
        schema = vol.Schema(
            {
                vol.Required("gateway_host_public_key"): str,
                vol.Required(
                    "gateway_primary_interface",
                    default=self.settings.get("gateway_primary_interface", ""),
                ): vol.All(str, vol.Match(r"^[A-Za-z][A-Za-z0-9_.-]{0,14}\Z")),
                vol.Required(
                    "gateway_secondary_interface",
                    default=self.settings.get("gateway_secondary_interface", ""),
                ): vol.All(str, vol.Match(r"^[A-Za-z][A-Za-z0-9_.-]{0,14}\Z")),
            }
        )
        if user_input is not None:
            try:
                values = schema(user_input)
                host_key = validate_host_key(values.pop("gateway_host_public_key"))
                if (
                    values["gateway_primary_interface"] == values["gateway_secondary_interface"]
                    or "lo" in values.values()
                ):
                    raise ValueError("Distinct WAN interfaces are required")
                directory = credential_directory(
                    self.hass, self.settings["gateway_observer_key_id"]
                )
                await self.hass.async_add_executor_job(
                    save_known_host, directory, self.settings["gateway_observer_host"], host_key
                )
                transport = GatewaySSHConfig(self.settings["gateway_observer_host"], directory)
                async with asyncio.timeout(15):
                    await self.hass.async_add_executor_job(transport.validate_files)
                    raw = await read_gateway(transport)
                parse_gateway_observation(
                    raw,
                    dt_util.utcnow(),
                    primary_interface=values["gateway_primary_interface"],
                    secondary_interface=values["gateway_secondary_interface"],
                )
                self.settings.update(values)
                return await self._after_gateway_observer()
            except (vol.Invalid, ValueError):
                errors["base"] = "invalid_observer_configuration"
            except (OSError, GatewayTransportError, NetworkDataError, TimeoutError):
                errors["base"] = "observer_unavailable"
        return self.async_show_form(
            step_id="gateway_observer",
            data_schema=schema,
            errors=errors,
            description_placeholders={"public_key": public_key},
        )

    async def _after_gateway_observer(self):
        if self.settings.get("ipv6_actor_enabled"):
            return await self.async_step_ipv6_controls()
        return await self.async_step_clients()

    async def async_step_gateway_management(self, user_input=None):
        """Rebind a pinned observer to the already trusted LAN management peer."""
        schema = vol.Schema(
            {vol.Required("use_existing_management_host"): selector.BooleanSelector()}
        )
        if user_input is None:
            return self.async_show_form(step_id="gateway_management", data_schema=schema)
        try:
            values = schema(user_input)
            if values["use_existing_management_host"] is not True:
                return self.async_abort(reason="no_change")
            settings = self.defaults
            if (
                settings.get("gateway_observer_enabled") is not True
                or settings.get("client_probe_enabled") is not True
            ):
                raise GatewayTransportError("Management peer unavailable")
            management = ClientProbeSSHConfig(
                settings["client_probe_host"],
                credential_directory(self.hass, settings["client_probe_key_id"]),
            )
            observer_dir = credential_directory(self.hass, settings["gateway_observer_key_id"])
            await self.hass.async_add_executor_job(
                bind_existing_gateway_host,
                observer_dir,
                management.credential_directory,
                management.host,
            )
            config = GatewaySSHConfig(management.host, observer_dir)
            raw = await read_gateway(config)
            parse_gateway_observation(
                raw,
                dt_util.utcnow(),
                primary_interface=settings["gateway_primary_interface"],
                secondary_interface=settings["gateway_secondary_interface"],
            )
            self.settings = {**settings, "gateway_observer_host": management.host}
            return self.finish()
        except (
            vol.Invalid,
            KeyError,
            OSError,
            ValueError,
            GatewayTransportError,
            NetworkDataError,
            TimeoutError,
        ):
            return self.async_show_form(
                step_id="gateway_management",
                data_schema=schema,
                errors={"base": "invalid_configuration"},
            )
