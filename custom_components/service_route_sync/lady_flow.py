"""Native Lady configuration with separate verified read-only source scopes."""

import asyncio
import json
from uuid import uuid4

import voluptuous as vol
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .backends.unifi import UniFiBackend
from .backends.dsl_ssh import DSLSSHConfig
from .backends.gateway_ssh import GatewayTransportError, read_gateway
from .backends.windows_ssh import WindowsArchiveSSHConfig, read_windows_archive
from .client_coordinator import validate_profiles
from .client_observation import canonical_mac
from .client_probe_protocol import parse_client_probes
from .core import SyncError
from .gateway_credentials import credential_directory, prepare_observer_key, save_known_host, validate_host_key
from .lady_dsl import parse_dsl
from .lady_probes import LadyClientSSHConfig


class LadyFlowSteps:
    async def async_step_lady(self, user_input=None):
        if not self.settings.get("lady_enabled"):
            return self.finish()
        existing = self.settings.get("lady_profile")
        assigned = {mac for profile in self.settings.get("client_profiles", []) for mac in profile["macs"]}
        choices = {}
        if existing:
            try:
                if tuple(validate_profiles([existing])) != ("ladyofgames",):
                    raise ValueError
            except ValueError:
                return self.async_abort(reason="invalid_lady_configuration")
            choices.update({mac: existing.get("name", "LadyOfGames") for mac in existing["macs"]})
        try:
            async with asyncio.timeout(8):
                rows = await UniFiBackend(self.hass, self.settings["unifi_entry_id"]).read_clients()
            for row in rows:
                mac = canonical_mac(row.get("mac"))
                if mac is not None and mac not in assigned:
                    choices.setdefault(mac, str(row.get("name") or row.get("hostname") or mac)[:128])
        except (SyncError, TimeoutError):
            if not choices:
                return self.async_abort(reason="router_unavailable")
        schema = vol.Schema({
            vol.Optional("lady_clients", default=existing["macs"] if existing else []): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[{"value": mac, "label": label} for mac, label in choices.items()], multiple=True)),
            vol.Optional("lady_manual_mac", default=""): selector.TextSelector(),
            vol.Optional("lady_reconfigure", default=False): selector.BooleanSelector(),
        })
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                selected = values.get("lady_clients", [])
                manual = values.get("lady_manual_mac", "").strip()
                if not isinstance(selected, list) or any(mac not in choices or mac in assigned for mac in selected):
                    raise ValueError
                macs = list(selected)
                if manual:
                    parsed = canonical_mac(manual)
                    if parsed is None or parsed in assigned:
                        raise ValueError
                    macs.append(parsed)
                if not 1 <= len(macs) <= 16 or len(set(macs)) != len(macs):
                    raise ValueError
                profile = {"id": "ladyofgames", "name": "LadyOfGames", "macs": macs}
                validate_profiles([profile])
                changed = existing is None or set(existing["macs"]) != set(macs)
                self.settings["lady_profile"] = profile
                if changed or values["lady_reconfigure"] or not all(self.settings.get("lady_" + role + "_key_id") for role in ("dsl", "pc", "windows")):
                    self.settings["lady_active"] = False
                    return await self.async_step_lady_sources()
                return self.finish()
            except (vol.Invalid, ValueError, TypeError):
                errors["base"] = "invalid_clients"
        return self.async_show_form(step_id="lady", data_schema=schema, errors=errors)

    async def async_step_lady_sources(self, user_input=None):
        public = {}
        try:
            for role in ("dsl", "pc", "windows"):
                key_id = self.settings.setdefault("lady_" + role + "_key_id", uuid4().hex)
                public[role + "_public_key"] = await prepare_observer_key(self.hass, key_id)
            if len({self.settings["lady_" + role + "_key_id"] for role in ("dsl", "pc", "windows")}) != 3:
                raise ValueError
        except (OSError, ValueError, GatewayTransportError, SyncError, TimeoutError):
            return self.async_abort(reason="observer_key_unavailable")
        # UniFi gateway.ip is the public WAN address and can change. Reuse the
        # independently verified, pinned management peer already used by HA.
        host = self.settings.get("client_probe_host")
        if (self.settings.get("client_probe_enabled") is not True
                or self.settings.get("gateway_observer_enabled") is not True
                or host != self.settings.get("gateway_observer_host")):
            return self.async_abort(reason="observer_address_unavailable")
        try:
            DSLSSHConfig(host, credential_directory(self.hass, self.settings["lady_dsl_key_id"]))
        except (ValueError, GatewayTransportError):
            return self.async_abort(reason="observer_address_unavailable")
        hosts = {host: host}
        schema = vol.Schema({
            vol.Required("lady_gateway_host"): selector.SelectSelector(selector.SelectSelectorConfig(options=[{"value": host, "label": label} for host, label in hosts.items()])),
            vol.Required("lady_windows_host"): selector.TextSelector(),
            vol.Required("gateway_host_public_key"): selector.TextSelector(),
            vol.Required("windows_host_public_key"): selector.TextSelector(),
        })
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                host = values["lady_gateway_host"]
                if host not in hosts:
                    raise ValueError
                configs = {
                    "dsl": DSLSSHConfig(host, credential_directory(self.hass, self.settings["lady_dsl_key_id"])),
                    "pc": LadyClientSSHConfig(host, credential_directory(self.hass, self.settings["lady_pc_key_id"])),
                    "windows": WindowsArchiveSSHConfig(values["lady_windows_host"], credential_directory(self.hass, self.settings["lady_windows_key_id"])),
                }
                pins = {"dsl": validate_host_key(values["gateway_host_public_key"]),
                        "pc": validate_host_key(values["gateway_host_public_key"]),
                        "windows": validate_host_key(values["windows_host_public_key"])}
                for role, config in configs.items():
                    await self.hass.async_add_executor_job(save_known_host, config.credential_directory, config.host, pins[role])
                    await self.hass.async_add_executor_job(config.validate_files)
                frame = json.dumps({"schema": 1, "addresses": {"ladyofgames": None}}).encode()
                results = await asyncio.gather(read_gateway(configs["dsl"], timeout=14),
                                               read_gateway(configs["pc"], timeout=20, input_data=frame),
                                               read_windows_archive(configs["windows"], None), return_exceptions=True)
                if any(isinstance(result, BaseException) for result in results):
                    raise GatewayTransportError("Lady source verification failed")
                parse_dsl(results[0], dt_util.utcnow())
                parse_client_probes(results[1], dt_util.utcnow(), ("ladyofgames",))
                self.settings.update(lady_gateway_host=host, lady_windows_host=values["lady_windows_host"], lady_active=False)
                return self.finish()
            except (OSError, GatewayTransportError, TimeoutError):
                errors["base"] = "observer_unavailable"
            except (ValueError, vol.Invalid):
                errors["base"] = "invalid_observer_configuration"
        return self.async_show_form(step_id="lady_sources", data_schema=schema, errors=errors, description_placeholders=public)
