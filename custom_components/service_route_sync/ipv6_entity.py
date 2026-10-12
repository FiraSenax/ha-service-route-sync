"""Small operational view of the verified IPv6 policy controller."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .ipv6_coordinator import IPv6Coordinator


class IPv6Entity(CoordinatorEntity[IPv6Coordinator]):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: IPv6Coordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_ipv6_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_network")},
            name="Internet connections",
            manufacturer="Service Route Sync",
            model="UniFi WAN monitoring",
        )

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return bool(super().available and data is not None
                    and timedelta(0) <= dt_util.utcnow() - data.observed_at <= timedelta(seconds=25))
