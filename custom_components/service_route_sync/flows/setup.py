from uuid import uuid4
import voluptuous as vol
from homeassistant import config_entries
from ..connection import requires_controller
from ..backends.unifi import UniFiBackend, read_controller
from ..routing.core import SyncError

from .fields import connection_fields, connection_settings, connection_identity, fields


class SetupSteps:
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
            self.settings = connection_settings(self.defaults, user_input)
            self.settings.setdefault("client_request_id", str(uuid4()))
            if isinstance(self, config_entries.ConfigFlow):
                await self.async_set_unique_id(connection_identity(self.settings))
                self._abort_if_unique_id_configured()
            elif connection_identity(self.settings) != connection_identity(self.defaults):
                errors["base"] = "immutable_router"
            if (
                not user_input["services"]
                and not self.settings.get("network_enabled")
                and not self.settings.get("client_monitoring_enabled")
                and not self.settings.get("work_probe_enabled")
                and not self.settings.get("monitoring_enabled")
                and not self.settings.get("wifi_history_enabled")
            ):
                errors["base"] = "no_services"
            elif not errors:
                try:
                    if requires_controller(self.settings):
                        await read_controller(
                            self.hass, self.settings, "read_devices", backend_factory=UniFiBackend
                        )
                except (SyncError, ValueError):
                    errors["base"] = "router_unavailable"
                else:
                    return await self.async_step_sources()
        return self.async_show_form(
            step_id="user", data_schema=fields(self.hass, self.defaults), errors=errors
        )

    async def async_step_reauth(self, entry_data):
        entry = self._get_reauth_entry()
        self.defaults = {**entry.data, **entry.options}
        return await self.async_step_connection()

    async def async_step_connection(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                values = vol.Schema(connection_fields(self.defaults))(user_input)
                settings = connection_settings(self.defaults, values)
                await read_controller(
                    self.hass, settings, "read_devices", backend_factory=UniFiBackend
                )
            except (vol.Invalid, ValueError, SyncError):
                errors["base"] = "router_unavailable"
            else:
                entry = self._get_reauth_entry()
                identity = connection_identity(settings)
                if entry.unique_id != identity:
                    await self.async_set_unique_id(identity)
                    self._abort_if_unique_id_configured()
                settings["automatic_apply"] = False
                return self.async_update_reload_and_abort(
                    entry, options=settings, unique_id=identity
                )
        return self.async_show_form(
            step_id="connection",
            data_schema=vol.Schema(connection_fields(self.defaults)),
            errors=errors,
        )
