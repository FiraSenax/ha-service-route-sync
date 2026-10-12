"""Stable client entities with explicit freshness and missing-value handling."""

from datetime import timedelta

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN


class ClientEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, profile_id, key):
        super().__init__(coordinator)
        self.profile_id = profile_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_client_{profile_id}_{key}"
        self._attr_translation_key = f"client_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_client_{profile_id}")},
            name=coordinator.profile_names[profile_id],
            manufacturer="Service Route Sync",
            model="UniFi client monitoring",
        )

    @property
    def observation(self):
        data = self.coordinator.data
        return data.clients.get(self.profile_id) if data else None

    @property
    def available(self):
        data = self.coordinator.data
        return bool(
            super().available
            and data is not None
            and self.observation is not None
            and timedelta(0) <= dt_util.utcnow() - data.observed_at <= timedelta(seconds=75)
        )
