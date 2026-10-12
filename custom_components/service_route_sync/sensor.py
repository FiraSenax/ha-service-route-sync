"""Compact operational state and read-only change preview."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfDataRate, UnitOfTime
from homeassistant.helpers.entity import EntityCategory

from .entity import RouteEntity
from .network_entity import NetworkDeviceEntity, NetworkEntity
from .client_entity import ClientEntity
from .lady_entity import LadyEntity
from .wifi_entity import WifiStatusSensor
from .frigate_entity import FrigateTimestampSensor
from .ipv6_entity import IPv6Entity
from datetime import datetime, timezone

FIELDS = {
    "status": "Status",
    "prefix_count": "Destination prefixes",
    "pending_routes": "Routes with changes",
    "last_success": "Last applied change",
}


async def async_setup_entry(hass, entry, async_add_entities):
    if coordinator := getattr(entry.runtime_data, 'ipv6_actor', None):
        async_add_entities(IPv6PolicySensor(coordinator, key, name) for key, name in (
            ("mode", "IPv6 policy mode"), ("decision", "IPv6 policy decision"),
            ("target", "IPv6 policy target"), ("observed_at", "IPv6 policy observed"),
            ("preview_target", "IPv6 policy preview target"),
            ("preview_reason", "IPv6 policy preview reason"),
        ))
    if coordinator := getattr(entry.runtime_data, 'frigate_actor', None):
        async_add_entities(FrigateTimestampSensor(coordinator, key) for key in ('last_observation', 'started'))
    if coordinator := getattr(entry.runtime_data, 'wifi', None):
        async_add_entities(WifiStatusSensor(coordinator, key) for key in ('client_count', 'last_observation'))
    if lady := getattr(entry.runtime_data, "lady", None):
        async_add_entities(LadyStatusSensor(lady, key) for key in ("mode", "pending", "active_incidents", "last_observation"))
        async_add_entities(ClientPortSensor(lady.clients, "ladyofgames", key) for key in (
            "speed_mbit_s", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped",
            "rx_bytes", "tx_bytes", "link_down_count",
        ))
    if coordinator := entry.runtime_data.clients:
        async_add_entities(
            ClientPortSensor(coordinator, profile_id, key)
            for profile_id in coordinator.profiles
            for key in (
                "speed_mbit_s",
                "rx_errors",
                "tx_errors",
                "rx_dropped",
                "tx_dropped",
                "rx_bytes",
                "tx_bytes",
                "link_down_count",
            )
        )
    if coordinator := entry.runtime_data.routes:
        async_add_entities(RouteSensor(coordinator, key, name) for key, name in FIELDS.items())
    if coordinator := entry.runtime_data.network:
        async_add_entities([NetworkUpdateSensor(coordinator)])
        async_add_entities(
            NetworkMetricSensor(coordinator, role, key)
            for role in ("primary", "secondary")
            for key in ("availability_pct", "latency_ms", "download_mbit_s", "upload_mbit_s")
        )


class IPv6PolicySensor(IPv6Entity, SensorEntity):
    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key, name)
        self.key = key
        if key == "observed_at":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        data = self.coordinator.data
        if data is None:
            return None
        return {
            "mode": data.actor.mode,
            "decision": data.plan.reason,
            "target": data.plan.target or "none",
            "observed_at": data.observed_at,
            "preview_target": data.preview.target or "none",
            "preview_reason": data.preview.reason,
        }[self.key]


class RouteSensor(RouteEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key, name)
        self.key = key
        if key == "last_success":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self.key)

    @property
    def extra_state_attributes(self):
        return (
            {"services": (self.coordinator.data or {}).get("services", {})}
            if self.key == "pending_routes"
            else None
        )


class LadyStatusSensor(LadyEntity, SensorEntity):
    def __init__(self, runtime, key):
        super().__init__(runtime, key)
        self.key = key
        if key == "last_observation":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif key == "mode":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["observation", "active"]

    @property
    def native_value(self):
        data = self.coordinator.data
        if data is None:
            return None
        if self.key == "mode":
            return "active" if self.runtime.active else "observation"
        if self.key == "last_observation":
            return datetime.fromtimestamp(data["observed_at"], timezone.utc)
        if self.key == "active_incidents":
            state = self.coordinator.transaction.store.state
            return sum(row["active"] for row in state["engine"].get("incidents", {}).values()) if state else None
        return data["pending"]


class ClientPortSensor(ClientEntity, SensorEntity):
    """Current wired metrics; historical offline port values are unavailable."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, profile_id, key):
        super().__init__(coordinator, profile_id, key)
        self.key = key
        if key == "speed_mbit_s":
            self._attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
            self._attr_device_class = SensorDeviceClass.DATA_RATE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif key.endswith("bytes"):
            self._attr_native_unit_of_measurement = "B"
            self._attr_device_class = SensorDeviceClass.DATA_SIZE
        # Switch counters can reset on reboot; never imply a monotonic lifetime total.

    @property
    def available(self):
        row = self.observation
        return bool(
            super().available
            and row.online
            and row.connection == "wired"
            and row.port is not None
            and row.port.up is True
            and self.native_value is not None
        )

    @property
    def native_value(self):
        row = self.observation
        return getattr(row.port, self.key, None) if row is not None and row.port else None


class NetworkMetricSensor(NetworkEntity, SensorEntity):
    """Numeric telemetry retains valid zero and exposes missing values as unavailable."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator, role, key):
        super().__init__(coordinator, role, key)
        self.key = key
        if key == "availability_pct":
            self._attr_native_unit_of_measurement = PERCENTAGE
        elif key == "latency_ms":
            self._attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
            self._attr_device_class = SensorDeviceClass.DURATION
        else:
            self._attr_native_unit_of_measurement = UnitOfDataRate.MEGABITS_PER_SECOND
            self._attr_device_class = SensorDeviceClass.DATA_RATE

    @property
    def available(self):
        return super().available and self.native_value is not None

    @property
    def native_value(self):
        return getattr(self.observation, self.key, None)


class NetworkUpdateSensor(NetworkDeviceEntity, SensorEntity):
    """Last complete controller observation, including unchanged measurements."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, "updated", "network_updated")

    @property
    def native_value(self):
        return self.coordinator.data.observed_at if self.coordinator.data is not None else None
