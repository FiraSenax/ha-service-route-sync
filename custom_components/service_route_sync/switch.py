"""Explicit opt-in for writes; defaults to preview only."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import RouteEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([RouteSwitch(entry.runtime_data, "automatic", "Automatic route updates")])


class RouteSwitch(RouteEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:routes"

    @property
    def is_on(self):
        return self.coordinator.settings.get("automatic_apply", False)

    async def async_turn_on(self, **kwargs):
        await self.coordinator.set_automatic(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.set_automatic(False)
