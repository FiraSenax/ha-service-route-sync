"""Explicit refresh and guarded recovery controls."""

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from .entity import RouteEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            RouteButton(entry.runtime_data, "refresh", "Refresh destinations"),
            RouteButton(entry.runtime_data, "restore", "Restore last route update"),
        ]
    )


class RouteButton(RouteEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self):
        try:
            if self._attr_unique_id.endswith("_restore"):
                await self.coordinator.restore_last()
            else:
                await self.coordinator.async_request_refresh()
        except Exception as exc:
            raise HomeAssistantError(str(exc)) from exc
