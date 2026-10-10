"""Compact operational state and read-only change preview."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import RouteEntity

FIELDS = {
    "status": "Status",
    "prefix_count": "Destination prefixes",
    "pending_routes": "Routes with changes",
    "last_success": "Last applied change",
}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(RouteSensor(entry.runtime_data, key, name) for key, name in FIELDS.items())


class RouteSensor(RouteEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key, name)
        self.key = key
        if key == "last_success":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        return self.coordinator.data.get(self.key)

    @property
    def extra_state_attributes(self):
        return (
            {"services": self.coordinator.data.get("services", {})}
            if self.key == "pending_routes"
            else None
        )
