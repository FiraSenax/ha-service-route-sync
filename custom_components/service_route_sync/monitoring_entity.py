"""Private incident details stay in storage; entities expose bounded status."""

from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN


class MonitoringEntity(CoordinatorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime, key):
        super().__init__(runtime.incidents)
        self.runtime = runtime
        entry_id = self.coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_monitoring_{key}"
        self._attr_translation_key = f"monitoring_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_monitoring")},
            name=runtime.profile_name,
            manufacturer="Service Route Sync",
            model="Network diagnostics",
        )

    @property
    def available(self):
        data = self.coordinator.data
        return bool(
            super().available
            and data is not None
            and 0 <= dt_util.utcnow().timestamp() - data["observed_at"] <= 75
        )
