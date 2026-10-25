from uuid import uuid4
import asyncio
import voluptuous as vol
from homeassistant.helpers import selector
from ..backends.unifi import UniFiBackend, read_controller
from ..const import DOMAIN
from ..routing.core import SyncError
from ..backends.gateway_ssh import GatewaySSHConfig, GatewayTransportError
from ..gateway_credentials import (
    credential_directory,
    prepare_observer_key,
    save_known_host,
    validate_host_key,
    same_pinned_gateway,
)
from ..network.network import NetworkDataError
from ..clients.client_coordinator import validate_profiles
from ..clients.client_observation import ClientDataError, canonical_mac
from ..clients.client_probe_protocol import parse_client_probes
from ..backends.client_probe_ssh import ClientProbeSSHConfig, read_client_probes
from homeassistant.util import dt as dt_util


class ClientsSteps:
    async def async_step_clients(self, user_input=None):
        """Select known identities, preserving existing grouped NIC profiles."""
        if not self.settings.get("client_monitoring_enabled"):
            self.settings["client_profiles"] = []
            self.settings["client_probe_enabled"] = False
            return await self.async_step_monitoring()
        existing = self.settings.get("client_profiles", [])
        try:
            if existing:
                validate_profiles(existing)
            async with asyncio.timeout(8):
                rows = await read_controller(
                    self.hass, self.settings, "read_clients", backend_factory=UniFiBackend
                )
        except (SyncError, ClientDataError, TimeoutError):
            return self.async_abort(reason="router_unavailable")
        profiles = {row["id"]: dict(row) for row in existing}
        assigned = (
            {mac for aliases in validate_profiles(existing).values() for mac in aliases}
            if existing
            else set()
        )
        for row in rows:
            mac = canonical_mac(row.get("mac"))
            if mac is None or mac in assigned:
                continue
            identity = "client_" + mac.replace(":", "")
            if identity in profiles:
                continue
            label = row.get("name") or row.get("hostname") or mac
            profiles[identity] = {"id": identity, "name": str(label)[:128], "macs": [mac]}
        if not profiles:
            return self.async_abort(reason="no_clients")
        choices = [{"value": key, "label": row.get("name") or key} for key, row in profiles.items()]
        schema = vol.Schema(
            {
                vol.Required(
                    "clients", default=[row["id"] for row in existing]
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=choices, multiple=True)
                ),
                vol.Optional(
                    "client_probe_enabled", default=self.settings.get("client_probe_enabled", False)
                ): selector.BooleanSelector(),
                vol.Optional("client_probe_reconfigure", default=False): selector.BooleanSelector(),
            }
        )
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                selected = values["clients"]
                if (
                    not selected
                    or len(set(selected)) != len(selected)
                    or any(key not in profiles for key in selected)
                ):
                    raise ClientDataError("Invalid client selection")
                candidate = [profiles[key] for key in selected]
                validate_profiles(candidate)
                self.settings["client_profiles"] = candidate
                self.settings["client_probe_enabled"] = values["client_probe_enabled"]
                if values["client_probe_enabled"] and (
                    values["client_probe_reconfigure"]
                    or not self.settings.get("client_probe_key_id")
                ):
                    self.settings["client_probe_key_id"] = uuid4().hex
                    return await self.async_step_client_probe()
                return await self.async_step_monitoring()
            except (vol.Invalid, ClientDataError):
                errors["base"] = "invalid_clients"
        return self.async_show_form(step_id="clients", data_schema=schema, errors=errors)

    async def async_step_client_probe(self, user_input=None):
        """A separate public key and pinned host, verified with a full live handshake."""
        errors = {}
        try:
            public_key = await prepare_observer_key(self.hass, self.settings["client_probe_key_id"])
            async with asyncio.timeout(8):
                gateways = await read_controller(
                    self.hass, self.settings, "read_gateways", backend_factory=UniFiBackend
                )
        except (OSError, GatewayTransportError, SyncError, TimeoutError, ValueError):
            return self.async_abort(reason="observer_key_unavailable")
        hosts = {}
        for row in gateways.values():
            host = row.get("ip")
            try:
                ClientProbeSSHConfig(
                    host, credential_directory(self.hass, self.settings["client_probe_key_id"])
                )
            except ValueError:
                continue
            hosts[host] = str(row.get("name") or host)
        if not hosts:
            return self.async_abort(reason="observer_address_unavailable")
        schema = vol.Schema(
            {
                vol.Required("client_probe_host"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": key, "label": label} for key, label in hosts.items()]
                    )
                ),
                vol.Required("gateway_host_public_key"): str,
            }
        )
        if user_input is not None:
            try:
                values = schema(user_input)
                host = values["client_probe_host"]
                if host not in hosts:
                    raise ValueError("Unknown gateway")
                key = validate_host_key(values["gateway_host_public_key"])
                directory = credential_directory(self.hass, self.settings["client_probe_key_id"])
                await self.hass.async_add_executor_job(save_known_host, directory, host, key)
                config = ClientProbeSSHConfig(host, directory)
                await self.hass.async_add_executor_job(config.validate_files)
                raw = await read_client_probes(config)
                parse_client_probes(
                    raw,
                    dt_util.utcnow(),
                    tuple(validate_profiles(self.settings["client_profiles"])),
                )
                self.settings["client_probe_host"] = host
                return await self.async_step_monitoring()
            except (OSError, GatewayTransportError, NetworkDataError):
                errors["base"] = "observer_unavailable"
            except (vol.Invalid, ValueError, ClientDataError):
                errors["base"] = "invalid_observer_configuration"
        return self.async_show_form(
            step_id="client_probe",
            data_schema=schema,
            errors=errors,
            description_placeholders={"public_key": public_key},
        )

    async def async_step_reconfigure(self, user_input=None):
        """Explicit one-time profile migration through HA's administrator UI/API."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None or entry.domain != DOMAIN:
            return self.async_abort(reason="invalid_client_import")
        if user_input is not None:
            if not isinstance(user_input, dict) or set(user_input) != {
                "profiles",
                "probe_key_id",
                "probe_host",
            }:
                return self.async_abort(reason="invalid_client_import")
            return await self.async_step_import(
                {
                    "entry_id": entry.entry_id,
                    "profiles": user_input["profiles"],
                    "probe_host": user_input["probe_host"],
                    "probe_key_id": user_input["probe_key_id"],
                }
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required("profiles"): selector.ObjectSelector(),
                    vol.Required("probe_host"): selector.TextSelector(),
                    vol.Required("probe_key_id"): selector.TextSelector(),
                }
            ),
        )

    async def async_step_import(self, user_input):
        """One-time authenticated migration; validate live access before saving options.

        Ordinary configuration remains the client selector UI. Import preserves
        offline profiles and grouped MAC aliases without editing HA storage.
        Trust material must already exist on HA from dedicated provisioning.
        """
        if not isinstance(user_input, dict) or set(user_input) != {
            "entry_id",
            "profiles",
            "probe_host",
            "probe_key_id",
        }:
            return self.async_abort(reason="invalid_client_import")
        if not isinstance(user_input["entry_id"], str):
            return self.async_abort(reason="invalid_client_import")
        entry = self.hass.config_entries.async_get_entry(user_input["entry_id"])
        if entry is None or entry.domain != DOMAIN:
            return self.async_abort(reason="invalid_client_import")
        settings = {**entry.data, **entry.options}
        try:
            profiles = validate_profiles(user_input["profiles"])
            if len(profiles) > 16:
                return self.async_abort(reason="invalid_client_import")
            directory = credential_directory(self.hass, user_input["probe_key_id"])
            config = ClientProbeSSHConfig(user_input["probe_host"], directory)
            await self.hass.async_add_executor_job(config.validate_files)
            existing = GatewaySSHConfig(
                settings["gateway_observer_host"],
                credential_directory(self.hass, settings["gateway_observer_key_id"]),
            )
            await self.hass.async_add_executor_job(existing.validate_files)
            if not await self.hass.async_add_executor_job(
                same_pinned_gateway, existing.known_hosts, config.known_hosts
            ):
                return self.async_abort(reason="invalid_client_import")
            raw = await read_client_probes(config)
            parse_client_probes(raw, dt_util.utcnow(), tuple(profiles))
        except (
            ClientDataError,
            GatewayTransportError,
            NetworkDataError,
            OSError,
            ValueError,
            KeyError,
        ):
            return self.async_abort(reason="invalid_client_import")
        # Closed JSON primitives copied into native options after full validation.
        candidate = [
            {"id": row["id"], "name": row.get("name", row["id"]), "macs": list(profiles[row["id"]])}
            for row in user_input["profiles"]
        ]
        self.hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                "client_monitoring_enabled": True,
                "client_profiles": candidate,
                "client_probe_enabled": True,
                "client_probe_host": user_input["probe_host"],
                "client_probe_key_id": user_input["probe_key_id"],
            },
        )
        return self.async_abort(reason="client_profiles_imported")
