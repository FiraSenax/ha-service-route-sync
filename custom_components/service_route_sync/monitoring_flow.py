"""Native Monitoring configuration with separate verified read-only source scopes."""

import asyncio
import json
from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .backends.unifi import UniFiBackend, read_controller
from .backends.dsl_ssh import DSLSSHConfig
from .backends.gateway_ssh import GatewayTransportError, read_gateway
from .backends.windows_ssh import WindowsArchiveSSHConfig, read_windows_archive
from .client_coordinator import validate_profiles
from .client_observation import canonical_mac
from .client_probe_protocol import parse_client_probes
from .core import SyncError
from .gateway_credentials import (
    credential_directory,
    prepare_observer_key,
    save_known_host,
    validate_host_key,
)
from .monitoring_dsl import parse_dsl
from .monitoring_dns import validate_dns_servers
from .monitoring_probes import MonitoringClientSSHConfig


class MonitoringFlowSteps:
    async def async_step_monitoring(self, user_input=None):
        if not self.settings.get("monitoring_enabled"):
            return self.finish()
        existing = self.settings.get("monitoring_profile")
        assigned = {
            mac for profile in self.settings.get("client_profiles", []) for mac in profile["macs"]
        }
        choices = {}
        if existing:
            try:
                if len(validate_profiles([existing])) != 1:
                    raise ValueError
            except ValueError:
                return self.async_abort(reason="invalid_monitoring_configuration")
            choices.update({mac: existing.get("name", existing["id"]) for mac in existing["macs"]})
        try:
            async with asyncio.timeout(8):
                rows = await read_controller(
                    self.hass, self.settings, "read_clients", backend_factory=UniFiBackend
                )
            for row in rows:
                mac = canonical_mac(row.get("mac"))
                if mac is not None and mac not in assigned:
                    choices.setdefault(
                        mac, str(row.get("name") or row.get("hostname") or mac)[:128]
                    )
        except (SyncError, TimeoutError):
            if not choices:
                return self.async_abort(reason="router_unavailable")
        schema = vol.Schema(
            {
                vol.Required(
                    "monitoring_profile_id", default=existing["id"] if existing else "client"
                ): str,
                vol.Required(
                    "monitoring_profile_name",
                    default=existing.get("name", existing["id"]) if existing else "Client",
                ): vol.All(str, vol.Length(min=1, max=128)),
                vol.Required(
                    "monitoring_expected_speed",
                    default=self.settings.get("monitoring_expected_speed", 1000),
                ): vol.All(int, vol.Range(min=1, max=100000)),
                vol.Required(
                    "monitoring_maintenance_hour",
                    default=self.settings.get("monitoring_maintenance_hour", -1),
                ): vol.All(int, vol.Range(min=-1, max=23)),
                vol.Required(
                    "monitoring_maintenance_minutes",
                    default=self.settings.get("monitoring_maintenance_minutes", 2),
                ): vol.All(int, vol.Range(min=1, max=60)),
                vol.Required(
                    "monitoring_windows_enabled",
                    default=self.settings.get("monitoring_windows_enabled", False),
                ): bool,
                vol.Optional(
                    "monitoring_notify_service",
                    default=self.settings.get("monitoring_notify_service", ""),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": "", "label": "Disabled"}]
                        + [
                            {"value": name, "label": "notify." + name}
                            for name in self.hass.services.async_services().get("notify", {})
                            if name != "send_message"
                        ]
                    )
                ),
                vol.Optional(
                    "monitoring_clients", default=existing["macs"] if existing else []
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": mac, "label": label} for mac, label in choices.items()],
                        multiple=True,
                    )
                ),
                vol.Optional("monitoring_manual_mac", default=""): selector.TextSelector(),
                vol.Optional("monitoring_reconfigure", default=False): selector.BooleanSelector(),
            }
        )
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                selected = values.get("monitoring_clients", [])
                manual = values.get("monitoring_manual_mac", "").strip()
                if not isinstance(selected, list) or any(
                    mac not in choices or mac in assigned for mac in selected
                ):
                    raise ValueError
                macs = list(selected)
                if manual:
                    parsed = canonical_mac(manual)
                    if parsed is None or parsed in assigned:
                        raise ValueError
                    macs.append(parsed)
                if not 1 <= len(macs) <= 16 or len(set(macs)) != len(macs):
                    raise ValueError
                profile = {
                    "id": values["monitoring_profile_id"],
                    "name": values["monitoring_profile_name"],
                    "macs": macs,
                }
                validate_profiles([profile])
                changed = existing != profile or values[
                    "monitoring_windows_enabled"
                ] != self.settings.get("monitoring_windows_enabled", False)
                if any(
                    row["id"] == profile["id"] for row in self.settings.get("client_profiles", [])
                ):
                    raise ValueError("Duplicate client profile")
                for key in (
                    "monitoring_expected_speed",
                    "monitoring_maintenance_hour",
                    "monitoring_maintenance_minutes",
                ):
                    self.settings[key] = values[key]
                self.settings["monitoring_windows_enabled"] = values["monitoring_windows_enabled"]
                self.settings["monitoring_notify_service"] = values["monitoring_notify_service"]
                if not values["monitoring_notify_service"]:
                    self.settings["monitoring_active"] = False
                self.settings["monitoring_profile"] = profile
                if (
                    changed
                    or values["monitoring_reconfigure"]
                    or not all(
                        self.settings.get("monitoring_" + role + "_key_id")
                        for role in (
                            ("dsl", "pc", "windows")
                            if self.settings.get("monitoring_windows_enabled")
                            else ("dsl", "pc")
                        )
                    )
                ):
                    self.settings["monitoring_active"] = False
                    return await self.async_step_monitoring_sources()
                return (
                    self.finish()
                    if self.settings.get("monitoring_dns_servers")
                    else await self.async_step_monitoring_dns()
                )
            except (vol.Invalid, ValueError, TypeError):
                errors["base"] = "invalid_clients"
        return self.async_show_form(step_id="monitoring", data_schema=schema, errors=errors)

    async def async_step_monitoring_sources(self, user_input=None):
        public = {}
        try:
            for role in (
                ("dsl", "pc", "windows")
                if self.settings.get("monitoring_windows_enabled")
                else ("dsl", "pc")
            ):
                key_id = self.settings.setdefault("monitoring_" + role + "_key_id", uuid4().hex)
                public[role + "_public_key"] = await prepare_observer_key(self.hass, key_id)
            if len(
                {
                    self.settings["monitoring_" + role + "_key_id"]
                    for role in (
                        ("dsl", "pc", "windows")
                        if self.settings.get("monitoring_windows_enabled")
                        else ("dsl", "pc")
                    )
                }
            ) != (3 if self.settings.get("monitoring_windows_enabled") else 2):
                raise ValueError
        except (OSError, ValueError, GatewayTransportError, SyncError, TimeoutError):
            return self.async_abort(reason="observer_key_unavailable")
        # UniFi gateway.ip is the public WAN address and can change. Reuse the
        # independently verified, pinned management peer already used by HA.
        host = self.settings.get("client_probe_host")
        if (
            self.settings.get("client_probe_enabled") is not True
            or self.settings.get("gateway_observer_enabled") is not True
            or host != self.settings.get("gateway_observer_host")
        ):
            return self.async_abort(reason="observer_address_unavailable")
        try:
            DSLSSHConfig(
                host, credential_directory(self.hass, self.settings["monitoring_dsl_key_id"])
            )
        except (ValueError, GatewayTransportError):
            return self.async_abort(reason="observer_address_unavailable")
        hosts = {host: host}
        schema = vol.Schema(
            {
                vol.Required("monitoring_gateway_host"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": host, "label": label} for host, label in hosts.items()]
                    )
                ),
                vol.Optional("monitoring_windows_host"): selector.TextSelector(),
                vol.Required("gateway_host_public_key"): selector.TextSelector(),
                vol.Optional("windows_host_public_key"): selector.TextSelector(),
            }
        )
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                host = values["monitoring_gateway_host"]
                if host not in hosts:
                    raise ValueError
                configs = {
                    "dsl": DSLSSHConfig(
                        host,
                        credential_directory(self.hass, self.settings["monitoring_dsl_key_id"]),
                    ),
                    "pc": MonitoringClientSSHConfig(
                        host, credential_directory(self.hass, self.settings["monitoring_pc_key_id"])
                    ),
                }
                if self.settings.get("monitoring_windows_enabled"):
                    configs["windows"] = WindowsArchiveSSHConfig(
                        values["monitoring_windows_host"],
                        credential_directory(self.hass, self.settings["monitoring_windows_key_id"]),
                    )
                pins = {
                    "dsl": validate_host_key(values["gateway_host_public_key"]),
                    "pc": validate_host_key(values["gateway_host_public_key"]),
                }
                if "windows" in configs:
                    pins["windows"] = validate_host_key(values["windows_host_public_key"])
                for role, config in configs.items():
                    await self.hass.async_add_executor_job(
                        save_known_host, config.credential_directory, config.host, pins[role]
                    )
                    await self.hass.async_add_executor_job(config.validate_files)
                frame = json.dumps(
                    {"schema": 1, "addresses": {self.settings["monitoring_profile"]["id"]: None}}
                ).encode()
                results = await asyncio.gather(
                    read_gateway(configs["dsl"], timeout=14),
                    read_gateway(configs["pc"], timeout=20, input_data=frame),
                    *(
                        [read_windows_archive(configs["windows"], None)]
                        if "windows" in configs
                        else []
                    ),
                    return_exceptions=True,
                )
                if any(isinstance(result, BaseException) for result in results):
                    raise GatewayTransportError("Monitoring source verification failed")
                parse_dsl(results[0], dt_util.utcnow())
                parse_client_probes(
                    results[1], dt_util.utcnow(), (self.settings["monitoring_profile"]["id"],)
                )
                self.settings.update(
                    monitoring_gateway_host=host,
                    monitoring_windows_host=values.get("monitoring_windows_host"),
                    monitoring_active=False,
                )
                return await self.async_step_monitoring_dns()
            except (OSError, GatewayTransportError, TimeoutError):
                errors["base"] = "observer_unavailable"
            except (ValueError, KeyError, vol.Invalid):
                errors["base"] = "invalid_observer_configuration"
        return self.async_show_form(
            step_id="monitoring_sources",
            data_schema=schema,
            errors=errors,
            description_placeholders={"windows_public_key": "—", **public},
        )

    async def async_step_monitoring_dns(self, user_input=None):
        """Choose two fixed LAN resolvers; callers cannot supply probe targets."""
        current = getattr(self, "settings", None) or getattr(self, "defaults", {})
        if not current.get("monitoring_enabled"):
            return self.async_abort(reason="monitoring_not_configured")
        previous = current.get("monitoring_dns_servers") or ("", "")
        schema = vol.Schema(
            {
                vol.Required("primary", default=previous[0]): selector.TextSelector(),
                vol.Required("secondary", default=previous[1]): selector.TextSelector(),
            }
        )
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                servers = validate_dns_servers((values["primary"], values["secondary"]))
                self.settings = {**current, "monitoring_dns_servers": list(servers)}
                return self.finish()
            except (vol.Invalid, ValueError):
                errors["base"] = "invalid_dns_servers"
        return self.async_show_form(step_id="monitoring_dns", data_schema=schema, errors=errors)
