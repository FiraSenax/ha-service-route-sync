"""Explicit current link state; rolling availability is a separate sensor."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .network_entity import NetworkEntity
from .gateway_entity import GatewayEntity
from .runtime import RouteConfigEntry
from .client_entity import ClientEntity
from .lady_entity import LadyEntity
from .frigate_entity import FrigateStatusBinarySensor
from .ipv6_entity import IPv6Entity


async def async_setup_entry(
    hass: HomeAssistant, entry: RouteConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    if coordinator := getattr(entry.runtime_data, 'ipv6_actor', None):
        async_add_entities([IPv6ReviewSensor(coordinator)])
    if coordinator := getattr(entry.runtime_data, 'frigate_actor', None):
        async_add_entities(FrigateStatusBinarySensor(coordinator, key) for key in ('connection', 'running', 'restart_enabled'))
    if lady := getattr(entry.runtime_data, "lady", None):
        async_add_entities([ClientOnlineSensor(lady.clients, "ladyofgames", "online")])
        async_add_entities(LadySourceSensor(lady, key) for key in ("api", "probe", "dns", "windows"))
        async_add_entities([LadyNotificationSensor(lady, "notification")])
    if coordinator := entry.runtime_data.clients:
        async_add_entities(
            ClientOnlineSensor(coordinator, key, "online") for key in coordinator.profiles
        )
    if coordinator := entry.runtime_data.network:
        async_add_entities(
            NetworkLinkSensor(coordinator, role, "link") for role in ("primary", "secondary")
        )
    if coordinator := entry.runtime_data.gateway:
        async_add_entities(
            GatewayProtocolSensor(coordinator, role, protocol)
            for role in ("primary", "secondary")
            for protocol in ("ipv4", "ipv6")
        )


class IPv6ReviewSensor(IPv6Entity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator):
        super().__init__(coordinator, "review_required", "IPv6 policy review required")

    @property
    def is_on(self):
        data = self.coordinator.data
        return bool(data.actor.review_required or data.local_latch) if data is not None else None


class GatewayProtocolSensor(GatewayEntity, BinarySensorEntity):
    """Unknown evidence is unavailable, never a confirmed protocol failure."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self):
        return self.health


class LadySourceSensor(LadyEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, runtime, source):
        super().__init__(runtime, "source_" + source)
        self.source = source

    @property
    def is_on(self):
        data = self.coordinator.data
        return data["sources"][self.source] if data else None


class LadyNotificationSensor(LadyEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def available(self):
        return super().available and self.runtime.active

    @property
    def is_on(self):
        data = self.coordinator.data
        return not data["notification_ok"] if data else None


class ClientOnlineSensor(ClientEntity, BinarySensorEntity):
    """Offline requires a complete fresh controller observation."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self):
        return self.observation.online if self.observation is not None else None


class NetworkLinkSensor(NetworkEntity, BinarySensorEntity):
    """Unavailable whenever the controller omits current link state."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.observation is not None
            and self.observation.link_up is not None
        )

    @property
    def is_on(self) -> bool | None:
        return self.observation.link_up if self.observation is not None else None
