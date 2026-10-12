"""Explicit service and existing route selection; writes disabled by default."""

from uuid import uuid4
import asyncio
import re
import time

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .backends.unifi import UniFiBackend
from .catalog import CATALOG
from .const import DEFAULTS, DOMAIN
from .core import SyncError, normalize_prefixes
from .backends.gateway_ssh import GatewaySSHConfig, GatewayTransportError, read_gateway
from .backends.ipv6_actor_ssh import IPv6ActorSSHConfig, inspect_actor
from .gateway_credentials import (
    credential_directory,
    prepare_observer_key,
    prepare_ipv6_actor_key,
    save_known_host,
    validate_host_key,
    same_pinned_gateway,
    bind_existing_gateway_host,
)
from .gateway_observation import parse_gateway_observation
from .network import NetworkDataError
from .client_coordinator import validate_profiles
from .client_observation import ClientDataError, canonical_mac
from .client_probe_protocol import parse_client_probes
from .backends.client_probe_ssh import ClientProbeSSHConfig, read_client_probes
from homeassistant.util import dt as dt_util
from .lady_flow import LadyFlowSteps
from .lady_history import LadyHistory, LadyHistoryError, journal_path
from .lady_store import LadyCheckpointError, LadyCheckpointStore
from .wifi_observation import observe_wireless, WifiDataError
from .frigate_flow import FrigateFlowSteps


def fields(hass, defaults):
    routers = [
        {"value": entry.entry_id, "label": entry.title}
        for entry in hass.config_entries.async_entries("unifi")
    ]
    services = [{"value": key, "label": service.name} for key, service in CATALOG.items()]
    return vol.Schema(
        {
            vol.Required(
                "unifi_entry_id", default=defaults.get("unifi_entry_id", "")
            ): selector.SelectSelector(selector.SelectSelectorConfig(options=routers)),
            vol.Required("services", default=defaults.get("services", [])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=services, multiple=True)
            ),
            vol.Optional(
                "network_enabled", default=defaults.get("network_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional(
                "client_monitoring_enabled", default=bool(defaults.get("client_profiles"))
            ): selector.BooleanSelector(),
            vol.Optional(
                "work_probe_enabled", default=defaults.get("work_probe_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional(
                "lady_enabled", default=defaults.get("lady_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional(
                "wifi_history_enabled", default=defaults.get("wifi_history_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional("ipv6", default=defaults.get("ipv6", True)): bool,
            vol.Optional(
                "network_metrics_enabled", default=defaults.get("network_metrics_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional(
                "maximum_prefixes", default=defaults.get("maximum_prefixes", 1000)
            ): vol.All(int, vol.Range(min=1, max=10000)),
            vol.Optional("maximum_removal", default=defaults.get("maximum_removal", 50)): vol.All(
                int, vol.Range(min=0, max=100)
            ),
            vol.Optional("interval_hours", default=defaults.get("interval_hours", 24)): vol.All(
                int, vol.Range(min=1, max=168)
            ),
        }
    )


class Steps(LadyFlowSteps):
    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                user_input = fields(self.hass, self.defaults)(user_input)
            except vol.Invalid:
                return self.async_show_form(
                    step_id="user",
                    data_schema=fields(self.hass, self.defaults),
                    errors={"base": "invalid_configuration"},
                )
            self.settings = {**self.defaults, **user_input}
            self.settings.setdefault("client_request_id", str(uuid4()))
            if isinstance(self, ConfigFlow):
                await self.async_set_unique_id(user_input["unifi_entry_id"])
                self._abort_if_unique_id_configured()
            elif user_input["unifi_entry_id"] != self.defaults["unifi_entry_id"]:
                errors["base"] = "immutable_router"
            if (
                not user_input["services"]
                and not self.settings.get("network_enabled")
                and not self.settings.get("client_monitoring_enabled")
                and not self.settings.get("work_probe_enabled")
                and not self.settings.get("lady_enabled")
                and not self.settings.get("wifi_history_enabled")
            ):
                errors["base"] = "no_services"
            elif not errors:
                return await self.async_step_sources()
        return self.async_show_form(
            step_id="user", data_schema=fields(self.hass, self.defaults), errors=errors
        )

    async def async_step_sources(self, user_input=None):
        schema = {}
        selected = self.settings["services"]
        wanted = []
        if any(CATALOG[key].provider == "m365" for key in selected):
            wanted.append("m365_categories")
            self.settings.setdefault("m365_categories", ["Optimize"])
        if "azure" in selected:
            wanted.append("azure_tags")
        if "aws" in selected:
            wanted.extend(("aws_regions", "aws_services"))
        wanted.extend(f"{key}_prefixes" for key in ("sap", "custom") if key in selected)
        if not wanted:
            return await self.async_step_routes()
        for key in wanted:
            if key == "m365_categories":
                schema[vol.Required(key, default=self.settings[key])] = selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["Optimize", "Allow", "Default"], multiple=True
                    )
                )
                continue
            schema[vol.Required(key, default=", ".join(self.settings.get(key, [])))] = str
        errors = {}
        if user_input is not None:
            try:
                for key in wanted:
                    raw = user_input[key]
                    values = (
                        raw
                        if isinstance(raw, list)
                        else [
                            part.strip()
                            for part in user_input[key].replace("\n", ",").split(",")
                            if part.strip()
                        ]
                    )
                    if not values:
                        raise SyncError("Empty selection")
                    if key.endswith("_prefixes"):
                        values = list(
                            normalize_prefixes(values, maximum=self.settings["maximum_prefixes"])
                        )
                    self.settings[key] = values
                return await self.async_step_routes()
            except SyncError:
                errors["base"] = "invalid_sources"
        return self.async_show_form(
            step_id="sources", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_routes(self, user_input=None):
        if not self.settings["services"]:
            return (
                self.finish()
                if getattr(self, "_route_only", False)
                else await self.async_step_network()
            )
        errors = {}
        try:
            routes = await UniFiBackend(self.hass, self.settings["unifi_entry_id"]).read_routes()
        except Exception:  # Authentication/API failures must keep the flow read-only.
            return self.async_abort(reason="router_unavailable")
        eligible = {
            key: route
            for key, route in routes.items()
            if route.get("matching_target") == "IP"
            and not route.get("domains")
            and not route.get("regions")
        }
        if not eligible:
            return self.async_abort(reason="no_routes")
        choices = [
            {"value": key, "label": route.get("description") or key}
            for key, route in eligible.items()
        ]
        schema = {}
        for key in self.settings["services"]:
            binding = self.settings.get("bindings", {}).get(key)
            field = vol.Required(key, default=binding) if binding in eligible else vol.Required(key)
            schema[field] = selector.SelectSelector(selector.SelectSelectorConfig(options=choices))
        if user_input is not None:
            if len(set(user_input.values())) != len(user_input):
                errors["base"] = "duplicate_routes"
            elif set(user_input) != set(self.settings["services"]) or any(
                value not in eligible for value in user_input.values()
            ):
                errors["base"] = "invalid_routes"
            else:
                self.settings["bindings"] = dict(user_input)
                return (
                    self.finish()
                    if getattr(self, "_route_only", False)
                    else await self.async_step_network()
                )
        return self.async_show_form(
            step_id="routes",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "services": ", ".join(CATALOG[key].name for key in self.settings["services"])
            },
        )

    async def async_step_network(self, user_input=None):
        """Select a gateway explicitly; enabling this feature performs only reads."""
        if not self.settings.get("network_enabled"):
            self.settings["gateway_observer_enabled"] = False
            self.settings["ipv6_actor_enabled"] = False
            self.settings["ipv6_actor_active"] = False
            return await self.async_step_clients()
        try:
            gateways = await UniFiBackend(
                self.hass, self.settings["unifi_entry_id"]
            ).read_gateways()
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
                    default=self.settings.get("gateway_primary_interface", "ppp0"),
                ): vol.All(str, vol.Match(r"^[A-Za-z][A-Za-z0-9_.-]{0,14}\Z")),
                vol.Required(
                    "gateway_secondary_interface",
                    default=self.settings.get("gateway_secondary_interface", "eth1"),
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
            if not self.settings.get("ipv6_actor_key_id"):
                self.settings["ipv6_actor_host"] = self.settings["gateway_observer_host"]
                self.settings["ipv6_actor_key_id"] = uuid4().hex
                # New keys are inspected first. Activation requires another
                # explicit options update after the old writer is stopped.
                self.settings["ipv6_actor_active"] = False
                return await self.async_step_ipv6_actor()
            if self.settings.get("ipv6_actor_host") != self.settings.get("gateway_observer_host"):
                return self.async_abort(reason="observer_address_unavailable")
        return await self.async_step_clients()

    async def async_step_ipv6_actor(self, user_input=None):
        """Provision a dedicated forced-command key; first handshake is read-only."""
        errors = {}
        try:
            public_key = await prepare_ipv6_actor_key(self.hass, self.settings["ipv6_actor_key_id"])
        except (OSError, GatewayTransportError, UnicodeError, ValueError):
            return self.async_abort(reason="observer_key_unavailable")
        schema = vol.Schema({vol.Required("gateway_host_public_key"): str})
        if user_input is not None:
            try:
                values = schema(user_input)
                host_key = validate_host_key(values["gateway_host_public_key"])
                directory = credential_directory(self.hass, self.settings["ipv6_actor_key_id"])
                observer_dir = credential_directory(
                    self.hass, self.settings["gateway_observer_key_id"]
                )
                await self.hass.async_add_executor_job(
                    save_known_host, directory, self.settings["ipv6_actor_host"], host_key
                )
                if not await self.hass.async_add_executor_job(
                    same_pinned_gateway, directory / "known_hosts", observer_dir / "known_hosts"
                ):
                    raise ValueError("Gateway host identity differs from observer")
                transport = IPv6ActorSSHConfig(self.settings["ipv6_actor_host"], directory)
                await self.hass.async_add_executor_job(transport.validate_files)
                await inspect_actor(transport)
                return (
                    self.finish()
                    if getattr(self, "_ipv6_only", False)
                    else await self.async_step_clients()
                )
            except (vol.Invalid, ValueError):
                errors["base"] = "invalid_observer_configuration"
            except (OSError, GatewayTransportError, TimeoutError):
                errors["base"] = "observer_unavailable"
        return self.async_show_form(
            step_id="ipv6_actor",
            data_schema=schema,
            errors=errors,
            description_placeholders={"public_key": public_key},
        )

    async def async_step_clients(self, user_input=None):
        """Select known identities, preserving existing grouped NIC profiles."""
        if not self.settings.get("client_monitoring_enabled"):
            self.settings["client_profiles"] = []
            self.settings["client_probe_enabled"] = False
            return await self.async_step_lady()
        existing = self.settings.get("client_profiles", [])
        try:
            if existing:
                validate_profiles(existing)
            async with asyncio.timeout(8):
                rows = await UniFiBackend(self.hass, self.settings["unifi_entry_id"]).read_clients()
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
                return await self.async_step_lady()
            except (vol.Invalid, ClientDataError):
                errors["base"] = "invalid_clients"
        return self.async_show_form(step_id="clients", data_schema=schema, errors=errors)

    async def async_step_client_probe(self, user_input=None):
        """A separate public key and pinned host, verified with a full live handshake."""
        errors = {}
        try:
            public_key = await prepare_observer_key(self.hass, self.settings["client_probe_key_id"])
            async with asyncio.timeout(8):
                gateways = await UniFiBackend(
                    self.hass, self.settings["unifi_entry_id"]
                ).read_gateways()
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
                return await self.async_step_lady()
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


class ConfigFlow(Steps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self.defaults = dict(DEFAULTS)

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

    def finish(self):
        return self.async_create_entry(title="Service Route Sync", data=self.settings)

    @staticmethod
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(FrigateFlowSteps, Steps, config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        self.defaults = {**DEFAULTS, **self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            return await self.async_step_user(user_input)
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "user",
                "route_services",
                "route_tls",
                "gateway_management",
                "ipv6_policy",
                "wifi_history",
                "frigate_actor",
                "lady_mode",
                "lady_dns",
            ],
        )

    async def async_step_route_tls(self, user_input=None):
        """Pin the exact UniFi certificate before this integration may write routes."""
        schema = vol.Schema(
            {
                vol.Required(
                    "unifi_tls_pin_sha256", default=self.defaults.get("unifi_tls_pin_sha256", "")
                ): selector.TextSelector(),
                vol.Optional("unifi_route_api_key"): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        errors = {}
        if user_input is not None:
            backend = None
            try:
                values = schema(user_input)
                pin = values["unifi_tls_pin_sha256"].replace(":", "").lower().strip()
                if not re.fullmatch(r"[0-9a-f]{64}", pin):
                    raise ValueError("Invalid SHA-256 fingerprint")
                api_key = values.get("unifi_route_api_key") or self.defaults.get(
                    "unifi_route_api_key"
                )
                backend = UniFiBackend(
                    self.hass,
                    self.defaults["unifi_entry_id"],
                    tls_pin_sha256=pin,
                    route_api_key=api_key,
                )
                routes = await backend.read_routes()
                if any(
                    route_id not in routes
                    for route_id in self.defaults.get("bindings", {}).values()
                ):
                    raise ValueError("A selected UniFi route is missing")
                self.settings = {**self.defaults, "unifi_tls_pin_sha256": pin}
                if api_key:
                    self.settings["unifi_route_api_key"] = api_key
                return self.finish()
            except (vol.Invalid, ValueError, KeyError, OSError, SyncError, TimeoutError):
                errors["base"] = "invalid_configuration"
            finally:
                if backend is not None:
                    await backend.async_close()
        return self.async_show_form(step_id="route_tls", data_schema=schema, errors=errors)

    async def async_step_ipv6_policy(self, user_input=None):
        """A narrow IPv6 management flow preserves all unrelated options."""
        schema = vol.Schema(
            {
                vol.Required(
                    "ipv6_actor_enabled", default=self.defaults.get("ipv6_actor_enabled", False)
                ): selector.BooleanSelector(),
                vol.Required(
                    "ipv6_actor_active", default=self.defaults.get("ipv6_actor_active", False)
                ): selector.BooleanSelector(),
                vol.Optional("ipv6_actor_reconfigure", default=False): selector.BooleanSelector(),
                vol.Optional("legacy_writer_stopped", default=False): selector.BooleanSelector(),
            }
        )
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                enabled, active = values["ipv6_actor_enabled"], values["ipv6_actor_active"]
                if enabled and self.defaults.get("gateway_observer_enabled") is not True:
                    raise ValueError("WAN observer required")
                if active and not enabled:
                    raise ValueError("Actor cannot write while disabled")
                if (
                    active
                    and not self.defaults.get("ipv6_actor_active")
                    and values.get("legacy_writer_stopped") is not True
                ):
                    errors["base"] = "ipv6_legacy_writer_running"
                if not errors and enabled:
                    fresh_key = values.get(
                        "ipv6_actor_reconfigure", False
                    ) or not self.defaults.get("ipv6_actor_key_id")
                    self.settings = {
                        **self.defaults,
                        "ipv6_actor_enabled": True,
                        "ipv6_actor_active": active and not fresh_key,
                    }
                    if fresh_key:
                        self.settings["ipv6_actor_host"] = self.defaults["gateway_observer_host"]
                        self.settings["ipv6_actor_key_id"] = uuid4().hex
                        self._ipv6_only = True
                        return await self.async_step_ipv6_actor()
                    if self.settings.get("ipv6_actor_host") != self.defaults.get(
                        "gateway_observer_host"
                    ):
                        raise ValueError("Gateway identity changed")
                    transport = IPv6ActorSSHConfig(
                        self.settings["ipv6_actor_host"],
                        credential_directory(self.hass, self.settings["ipv6_actor_key_id"]),
                    )
                    await self.hass.async_add_executor_job(transport.validate_files)
                    actor = await inspect_actor(transport)
                    if active and (
                        not actor.writable or actor.review_required or not actor.healthy
                    ):
                        errors["base"] = "observer_unavailable"
                    elif not errors:
                        return self.finish()
                elif not errors:
                    self.settings = {
                        **self.defaults,
                        "ipv6_actor_enabled": False,
                        "ipv6_actor_active": False,
                    }
                    return self.finish()
            except (
                vol.Invalid,
                ValueError,
                KeyError,
                OSError,
                GatewayTransportError,
                TimeoutError,
            ):
                errors["base"] = "invalid_configuration"
        return self.async_show_form(step_id="ipv6_policy", data_schema=schema, errors=errors)

    async def async_step_lady_mode(self, user_input=None):
        """Activate only an imported, durable Lady checkpoint after legacy freeze."""
        if not self.defaults.get("lady_enabled"):
            return self.async_abort(reason="lady_not_configured")
        schema = vol.Schema(
            {
                vol.Required(
                    "lady_active", default=self.defaults.get("lady_active", False)
                ): selector.BooleanSelector(),
                vol.Optional("legacy_monitor_stopped", default=False): selector.BooleanSelector(),
            }
        )
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                active = values["lady_active"]
                if active and not self.defaults.get("lady_active"):
                    if values["legacy_monitor_stopped"] is not True:
                        errors["base"] = "legacy_monitor_running"
                    else:
                        runtime = getattr(self.config_entry, "runtime_data", None)
                        lady = getattr(runtime, "lady", None)
                        if lady is None or not lady.incidents.last_update_success:
                            errors["base"] = "lady_sources_not_ready"
                        else:
                            store = LadyCheckpointStore(self.hass, self.config_entry.entry_id)
                            checkpoint = await store.async_load()
                            if (
                                checkpoint is None
                                or not isinstance(checkpoint.get("cursor"), dict)
                                or "day" not in checkpoint["cursor"]
                                or not checkpoint.get("history_epoch")
                                or not checkpoint.get("history_revision")
                            ):
                                errors["base"] = "lady_checkpoint_missing"
                            else:
                                history = LadyHistory(
                                    journal_path(
                                        self.hass.config.path(DOMAIN, "lady_history"),
                                        store.store.key,
                                        checkpoint["history_epoch"],
                                    )
                                )
                                await self.hass.async_add_executor_job(
                                    history.snapshot, checkpoint["history_revision"], time.time()
                                )
                if not errors:
                    self.settings = {**self.defaults, "lady_active": active}
                    return self.finish()
            except (
                vol.Invalid,
                LadyCheckpointError,
                LadyHistoryError,
                OSError,
                ValueError,
                TypeError,
            ):
                errors["base"] = "lady_checkpoint_missing"
        return self.async_show_form(step_id="lady_mode", data_schema=schema, errors=errors)

    async def async_step_route_services(self, user_input=None):
        """Edit route sources and bindings without reprovisioning network or actors."""
        schema = vol.Schema(
            {
                vol.Required(
                    "services", default=self.defaults.get("services", [])
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": key, "label": service.name}
                            for key, service in CATALOG.items()
                        ],
                        multiple=True,
                    )
                ),
            }
        )
        if user_input is None:
            return self.async_show_form(step_id="route_services", data_schema=schema)
        try:
            values = schema(user_input)
            chosen = values["services"]
            if len(chosen) != len(set(chosen)) or any(key not in CATALOG for key in chosen):
                raise vol.Invalid("Invalid route service selection")
            self.settings = {**self.defaults, "services": chosen}
            self._route_only = True
            return await self.async_step_sources()
        except vol.Invalid:
            return self.async_show_form(
                step_id="route_services",
                data_schema=schema,
                errors={"base": "invalid_configuration"},
            )

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

    async def async_step_wifi_history(self, user_input=None):
        """Change only wireless sampling without reprovisioning existing SSH scopes."""
        schema = vol.Schema(
            {
                vol.Required(
                    "wifi_history_enabled", default=self.defaults.get("wifi_history_enabled", False)
                ): selector.BooleanSelector()
            }
        )
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                if values["wifi_history_enabled"]:
                    async with asyncio.timeout(8):
                        rows = await UniFiBackend(
                            self.hass, self.defaults["unifi_entry_id"]
                        ).read_clients()
                    observe_wireless(rows, dt_util.utcnow())
                self.settings = {**self.defaults, **values}
                return self.finish()
            except (vol.Invalid, SyncError, WifiDataError, TimeoutError):
                errors["base"] = "invalid_configuration"
        return self.async_show_form(step_id="wifi_history", data_schema=schema, errors=errors)

    def finish(self):
        # Only route-policy changes require a fresh manual enablement. Editing
        # an independent IPv6, Lady, Frigate or Wi-Fi feature must not silently
        # stop the already reviewed daily route updates.
        route_fields = (
            "services",
            "bindings",
            "m365_categories",
            "azure_tags",
            "aws_regions",
            "aws_services",
            "sap_prefixes",
            "custom_prefixes",
            "maximum_removal",
            "maximum_prefixes",
            "ipv6",
            "interval_hours",
            "unifi_tls_pin_sha256",
            "unifi_route_api_key",
        )
        if any(self.settings.get(key) != self.defaults.get(key) for key in route_fields):
            self.settings["automatic_apply"] = False
        return self.async_create_entry(title="", data=self.settings)
