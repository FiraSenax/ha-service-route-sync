"""Aggregate Wi-Fi status without exposing client identifiers to recorder."""

from datetime import timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN


class WifiStatusSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self.key = key
        identity = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{identity}_wifi_{key}"
        self._attr_translation_key = f"wifi_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{identity}_wifi")},
            name="Wi-Fi history",
            manufacturer="Service Route Sync",
            model="UniFi wireless monitoring",
        )
        if key == "last_observation":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def available(self):
        data = self.coordinator.data
        return bool(
            super().available
            and data is not None
            and data.observed_at.tzinfo is not None
            and timedelta(0) <= dt_util.utcnow() - data.observed_at <= timedelta(seconds=75)
        )

    @property
    def native_value(self):
        data = self.coordinator.data
        if data is None:
            return None
        return data.observed_at if self.key == "last_observation" else len(data.clients)
