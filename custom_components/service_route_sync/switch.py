"""Explicit opt-in for writes; defaults to preview only."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory

from .routing.entity import RouteEntity
from .security import require_admin


async def async_setup_entry(hass, entry, async_add_entities):
    if coordinator := entry.runtime_data.routes:
        async_add_entities([RouteSwitch(coordinator, "automatic", "Automatic route updates")])


class RouteSwitch(RouteEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:routes"

    @property
    def is_on(self):
        return self.coordinator.settings.get("automatic_apply", False)

    async def async_turn_on(self, **kwargs):
        await require_admin(self.hass, self._context)
        await self.coordinator.set_automatic(True)

    async def async_turn_off(self, **kwargs):
        await require_admin(self.hass, self._context)
        await self.coordinator.set_automatic(False)
