"""Explicit refresh and guarded recovery controls."""

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from .routing.entity import RouteEntity
from .security import require_admin
from .routing.core import SyncError


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.runtime_data.routes is None:
        return
    async_add_entities(
        [
            RouteButton(entry.runtime_data.routes, "refresh", "Refresh destinations"),
            RouteButton(entry.runtime_data.routes, "restore", "Restore last route update"),
        ]
    )


class RouteButton(RouteEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self):
        # A refresh can apply a route plan when automatic writes are enabled.
        await require_admin(self.hass, self._context)
        try:
            if self._attr_unique_id.endswith("_restore"):
                await self.coordinator.restore_last()
            else:
                await self.coordinator.async_request_refresh()
        except SyncError as exc:
            raise HomeAssistantError(str(exc)) from None
        except Exception:
            raise HomeAssistantError("Route operation could not be completed") from None
