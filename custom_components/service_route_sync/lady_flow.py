"""Native Lady configuration with separate verified read-only source scopes."""

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
from .lady_dsl import parse_dsl
from .lady_dns import validate_dns_servers
from .lady_probes import LadyClientSSHConfig


class LadyFlowSteps:
    async def async_step_lady(self, user_input=None):
        if not self.settings.get("lady_enabled"):
            return self.finish()
        existing = self.settings.get("lady_profile")
        assigned = {
            mac for profile in self.settings.get("client_profiles", []) for mac in profile["macs"]
        }
        choices = {}
        if existing:
            try:
                if len(validate_profiles([existing])) != 1:
                    raise ValueError
            except ValueError:
                return self.async_abort(reason="invalid_lady_configuration")
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
                    "lady_profile_id", default=existing["id"] if existing else "client"
                ): str,
                vol.Required(
                    "lady_profile_name",
                    default=existing.get("name", existing["id"]) if existing else "Client",
                ): vol.All(str, vol.Length(min=1, max=128)),
                vol.Required(
                    "lady_expected_speed", default=self.settings.get("lady_expected_speed", 1000)
                ): vol.All(int, vol.Range(min=1, max=100000)),
                vol.Required(
                    "lady_maintenance_hour", default=self.settings.get("lady_maintenance_hour", -1)
                ): vol.All(int, vol.Range(min=-1, max=23)),
                vol.Required(
                    "lady_maintenance_minutes",
                    default=self.settings.get("lady_maintenance_minutes", 2),
                ): vol.All(int, vol.Range(min=1, max=60)),
                vol.Required(
                    "lady_windows_enabled", default=self.settings.get("lady_windows_enabled", False)
                ): bool,
                vol.Optional(
                    "lady_notify_service", default=self.settings.get("lady_notify_service", "")
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
                    "lady_clients", default=existing["macs"] if existing else []
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": mac, "label": label} for mac, label in choices.items()],
                        multiple=True,
                    )
                ),
                vol.Optional("lady_manual_mac", default=""): selector.TextSelector(),
                vol.Optional("lady_reconfigure", default=False): selector.BooleanSelector(),
            }
        )
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                selected = values.get("lady_clients", [])
                manual = values.get("lady_manual_mac", "").strip()
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
                    "id": values["lady_profile_id"],
                    "name": values["lady_profile_name"],
                    "macs": macs,
                }
                validate_profiles([profile])
                changed = existing != profile or values[
                    "lady_windows_enabled"
                ] != self.settings.get("lady_windows_enabled", False)
                if any(
                    row["id"] == profile["id"] for row in self.settings.get("client_profiles", [])
                ):
                    raise ValueError("Duplicate client profile")
                for key in (
                    "lady_expected_speed",
                    "lady_maintenance_hour",
                    "lady_maintenance_minutes",
                ):
                    self.settings[key] = values[key]
                self.settings["lady_windows_enabled"] = values["lady_windows_enabled"]
                self.settings["lady_notify_service"] = values["lady_notify_service"]
                if not values["lady_notify_service"]:
                    self.settings["lady_active"] = False
                self.settings["lady_profile"] = profile
                if (
                    changed
                    or values["lady_reconfigure"]
                    or not all(
                        self.settings.get("lady_" + role + "_key_id")
                        for role in (
                            ("dsl", "pc", "windows")
                            if self.settings.get("lady_windows_enabled")
                            else ("dsl", "pc")
                        )
                    )
                ):
                    self.settings["lady_active"] = False
                    return await self.async_step_lady_sources()
                return (
                    self.finish()
                    if self.settings.get("lady_dns_servers")
                    else await self.async_step_lady_dns()
                )
            except (vol.Invalid, ValueError, TypeError):
                errors["base"] = "invalid_clients"
        return self.async_show_form(step_id="lady", data_schema=schema, errors=errors)

    async def async_step_lady_sources(self, user_input=None):
        public = {}
        try:
            for role in (
                ("dsl", "pc", "windows")
                if self.settings.get("lady_windows_enabled")
                else ("dsl", "pc")
            ):
                key_id = self.settings.setdefault("lady_" + role + "_key_id", uuid4().hex)
                public[role + "_public_key"] = await prepare_observer_key(self.hass, key_id)
            if len(
                {
                    self.settings["lady_" + role + "_key_id"]
                    for role in (
                        ("dsl", "pc", "windows")
                        if self.settings.get("lady_windows_enabled")
                        else ("dsl", "pc")
                    )
                }
            ) != (3 if self.settings.get("lady_windows_enabled") else 2):
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
            DSLSSHConfig(host, credential_directory(self.hass, self.settings["lady_dsl_key_id"]))
        except (ValueError, GatewayTransportError):
            return self.async_abort(reason="observer_address_unavailable")
        hosts = {host: host}
        schema = vol.Schema(
            {
                vol.Required("lady_gateway_host"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": host, "label": label} for host, label in hosts.items()]
                    )
                ),
                vol.Optional("lady_windows_host"): selector.TextSelector(),
                vol.Required("gateway_host_public_key"): selector.TextSelector(),
                vol.Optional("windows_host_public_key"): selector.TextSelector(),
            }
        )
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                host = values["lady_gateway_host"]
                if host not in hosts:
                    raise ValueError
                configs = {
                    "dsl": DSLSSHConfig(
                        host, credential_directory(self.hass, self.settings["lady_dsl_key_id"])
                    ),
                    "pc": LadyClientSSHConfig(
                        host, credential_directory(self.hass, self.settings["lady_pc_key_id"])
                    ),
                }
                if self.settings.get("lady_windows_enabled"):
                    configs["windows"] = WindowsArchiveSSHConfig(
                        values["lady_windows_host"],
                        credential_directory(self.hass, self.settings["lady_windows_key_id"]),
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
                    {"schema": 1, "addresses": {self.settings["lady_profile"]["id"]: None}}
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
                    raise GatewayTransportError("Lady source verification failed")
                parse_dsl(results[0], dt_util.utcnow())
                parse_client_probes(
                    results[1], dt_util.utcnow(), (self.settings["lady_profile"]["id"],)
                )
                self.settings.update(
                    lady_gateway_host=host,
                    lady_windows_host=values.get("lady_windows_host"),
                    lady_active=False,
                )
                return await self.async_step_lady_dns()
            except (OSError, GatewayTransportError, TimeoutError):
                errors["base"] = "observer_unavailable"
            except (ValueError, KeyError, vol.Invalid):
                errors["base"] = "invalid_observer_configuration"
        return self.async_show_form(
            step_id="lady_sources",
            data_schema=schema,
            errors=errors,
            description_placeholders={"windows_public_key": "—", **public},
        )

    async def async_step_lady_dns(self, user_input=None):
        """Choose two fixed LAN resolvers; callers cannot supply probe targets."""
        current = getattr(self, "settings", None) or getattr(self, "defaults", {})
        if not current.get("lady_enabled"):
            return self.async_abort(reason="lady_not_configured")
        previous = current.get("lady_dns_servers") or ("", "")
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
                self.settings = {**current, "lady_dns_servers": list(servers)}
                return self.finish()
            except (vol.Invalid, ValueError):
                errors["base"] = "invalid_dns_servers"
        return self.async_show_form(step_id="lady_dns", data_schema=schema, errors=errors)
