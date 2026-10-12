"""Minimal operational status for the restricted Frigate bridge."""

from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN


class FrigateEntity(CoordinatorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self.key = key
        identity = coordinator.entry.entry_id
        self._attr_unique_id = f"{identity}_frigate_control_{key}"
        self._attr_translation_key = f"frigate_control_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{identity}_frigate_control")},
            name="Frigate control",
            manufacturer="Service Route Sync",
        )

    @property
    def available(self):
        data = self.coordinator.data
        return bool(
            super().available
            and self.coordinator.binding_current
            and data is not None
            and data.observed_at.tzinfo is not None
            and timedelta(0) <= dt_util.utcnow() - data.observed_at <= timedelta(seconds=75)
        )


class FrigateStatusBinarySensor(FrigateEntity, BinarySensorEntity):
    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        if key == "connection":
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self):
        if self.coordinator.data is None:
            return None
        if self.key == "connection":
            return True
        if self.key == "restart_enabled":
            return self.coordinator.restart_enabled
        return self.coordinator.data.running


class FrigateTimestampSensor(FrigateEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        data = self.coordinator.data
        if data is None:
            return None
        return data.observed_at if self.key == "last_observation" else data.started_at
