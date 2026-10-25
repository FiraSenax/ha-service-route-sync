import asyncio
import voluptuous as vol
from homeassistant.helpers import selector
from ..backends.unifi import UniFiBackend, read_controller
from ..routing.core import SyncError
from homeassistant.util import dt as dt_util
from ..wifi.wifi_observation import observe_wireless, WifiDataError


class WifiSteps:
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
                        rows = await read_controller(
                            self.hass, self.defaults, "read_clients", backend_factory=UniFiBackend
                        )
                    observe_wireless(rows, dt_util.utcnow())
                self.settings = {**self.defaults, **values}
                return self.finish()
            except (vol.Invalid, SyncError, WifiDataError, TimeoutError):
                errors["base"] = "invalid_configuration"
        return self.async_show_form(step_id="wifi_history", data_schema=schema, errors=errors)
