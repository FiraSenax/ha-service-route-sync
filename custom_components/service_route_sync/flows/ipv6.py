from uuid import uuid4
import voluptuous as vol
from homeassistant.helpers import selector
from ..backends.gateway_ssh import GatewayTransportError
from ..backends.ipv6_actor_ssh import IPv6ActorSSHConfig, inspect_actor
from ..gateway_credentials import (
    credential_directory,
    prepare_ipv6_actor_key,
    save_known_host,
    validate_host_key,
    same_pinned_gateway,
)

from .fields import ipv6_control_fields, validate_ipv6_controls


class Ipv6Steps:
    async def async_step_ipv6_controls(self, user_input=None):
        schema = vol.Schema(ipv6_control_fields(self.settings))
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                candidate = {**self.settings, **values}
                validate_ipv6_controls(candidate)
                self.settings = candidate
                return await self._after_ipv6_controls()
            except (vol.Invalid, ValueError):
                errors["base"] = "invalid_configuration"
        return self.async_show_form(step_id="ipv6_controls", data_schema=schema, errors=errors)

    async def _after_ipv6_controls(self):
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

    async def async_step_ipv6_policy(self, user_input=None):
        """A narrow IPv6 management flow preserves all unrelated options."""
        schema = vol.Schema(
            {
                **ipv6_control_fields(self.defaults),
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
                if enabled:
                    validate_ipv6_controls({**self.defaults, **values})
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
                        **{key: value for key, value in values.items() if key.startswith("ipv6_")},
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
                        **{key: value for key, value in values.items() if key.startswith("ipv6_")},
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
