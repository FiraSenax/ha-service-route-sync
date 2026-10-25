"""Protocol diagnostics share the WAN device, never expose delegated prefixes."""

from datetime import timedelta

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from .gateway_coordinator import GatewayCoordinator


class GatewayEntity(CoordinatorEntity[GatewayCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator, role, protocol):
        super().__init__(coordinator)
        self.role = role
        self.protocol = protocol
        self._attr_unique_id = f"{coordinator.entry.entry_id}_gateway_{role}_{protocol}"
        self._attr_translation_key = f"wan_{protocol}_connectivity"
        self._attr_translation_placeholders = {
            "wan": coordinator.settings.get(f"network_{role}_label", role.title())
        }
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_network")},
            name="Internet connections",
            manufacturer="Service Route Sync",
            model="UniFi WAN monitoring",
        )

    @property
    def observation(self):
        data = self.coordinator.data
        return getattr(data, self.role, None) if data is not None else None

    @property
    def available(self):
        data = self.coordinator.data
        sample = self.observation
        if not super().available or data is None or sample is None:
            return False
        now = dt_util.utcnow()
        return (
            all(
                timestamp.tzinfo is not None
                and timedelta(0) <= now - timestamp <= timedelta(seconds=20)
                for timestamp in (data.observed_at, sample.observed_at)
            )
            and self.health is not None
        )

    @property
    def health(self):
        sample = self.observation
        if sample is None:
            return None
        if self.protocol == "ipv6":
            return sample.ipv6_health(require_delegation=self.role == "primary").healthy
        return sample.ipv4_health().healthy
