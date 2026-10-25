"""Independent feature lifecycles for one independently authenticated config entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .routing.coordinator import RouteCoordinator
    from .network.network_coordinator import NetworkCoordinator
    from .network.gateway_coordinator import GatewayCoordinator
    from .clients.client_coordinator import ClientCoordinator
    from .clients.client_probe_coordinator import ClientProbeCoordinator
    from .probes.work_probe_coordinator import WorkProbeCoordinator
    from .monitoring.monitoring_runtime import MonitoringRuntime
    from .wifi.wifi_coordinator import WifiCoordinator
    from .frigate.frigate_coordinator import FrigateActorCoordinator
    from .ipv6.ipv6_coordinator import IPv6Coordinator


@dataclass(slots=True)
class RuntimeData:
    """A public-feed failure must not stop local WAN observations."""

    routes: RouteCoordinator | None = None
    network: NetworkCoordinator | None = None
    gateway: GatewayCoordinator | None = None
    clients: ClientCoordinator | None = None
    client_probes: ClientProbeCoordinator | None = None
    work_probes: WorkProbeCoordinator | None = None
    monitoring: MonitoringRuntime | None = None
    wifi: WifiCoordinator | None = None
    frigate_actor: FrigateActorCoordinator | None = None
    ipv6_actor: IPv6Coordinator | None = None

    setup_issues: dict[str, str] = field(default_factory=dict)

    def coordinators(self):
        """Single lifecycle inventory; feature removal cannot orphan a poller."""
        values = [
            self.routes,
            self.network,
            self.gateway,
            self.clients,
            self.client_probes,
            self.work_probes,
            self.wifi,
            self.frigate_actor,
            self.ipv6_actor,
        ]
        if self.monitoring is not None:
            values.extend((self.monitoring.clients, self.monitoring.incidents))
        return tuple(value for value in values if value is not None)

    async def shutdown(self):
        for coordinator in reversed(self.coordinators()):
            await coordinator.async_shutdown()


type RouteConfigEntry = ConfigEntry[RuntimeData]
