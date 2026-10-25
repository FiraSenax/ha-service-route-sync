"""Stable WAN identities and a grouped, translated device in the HA UI."""

from __future__ import annotations

from typing import Literal
from datetime import timedelta

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from .network import WanObservation
from .network_coordinator import NetworkCoordinator


class NetworkDeviceEntity(CoordinatorEntity[NetworkCoordinator]):
    """Common identity and freshness for the WAN device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NetworkCoordinator, key: str, translation_key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_network_{key}"
        self._attr_translation_key = translation_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_network")},
            name="Internet connections",
            manufacturer="Service Route Sync",
            model="UniFi WAN monitoring",
        )

    @property
    def available(self) -> bool:
        """Do not expose retained values after a delayed or failed refresh."""
        data = self.coordinator.data
        if not super().available or data is None:
            return False
        maximum_age = timedelta(
            seconds=max(20, 2 * self.coordinator.settings.get("network_interval_seconds", 10))
        )
        now = dt_util.utcnow()
        return (
            data.observed_at.tzinfo is not None
            and timedelta(0) <= now - data.observed_at <= maximum_age
        )


class NetworkEntity(NetworkDeviceEntity):
    """One optional observation; an absent WAN never becomes zero/false."""

    def __init__(
        self,
        coordinator: NetworkCoordinator,
        role: Literal["primary", "secondary"],
        key: str,
    ) -> None:
        super().__init__(coordinator, f"{role}_{key}", f"wan_{key}")
        self.role = role
        self._attr_translation_placeholders = {
            "wan": coordinator.settings.get(f"network_{role}_label", role.title())
        }

    @property
    def available(self) -> bool:
        return super().available and self.observation is not None

    @property
    def observation(self) -> WanObservation | None:
        data = self.coordinator.data
        return getattr(data, self.role, None) if data is not None else None
