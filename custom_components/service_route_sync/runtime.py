"""Independent feature lifecycles for one authenticated UniFi parent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import RouteCoordinator
    from .network_coordinator import NetworkCoordinator
    from .gateway_coordinator import GatewayCoordinator
    from .client_coordinator import ClientCoordinator
    from .client_probe_coordinator import ClientProbeCoordinator
    from .work_probe_coordinator import WorkProbeCoordinator
    from .lady_runtime import LadyRuntime
    from .wifi_coordinator import WifiCoordinator
    from .frigate_coordinator import FrigateActorCoordinator
    from .ipv6_coordinator import IPv6Coordinator


@dataclass(slots=True)
class RuntimeData:
    """A public-feed failure must not stop local WAN observations."""

    routes: RouteCoordinator | None = None
    network: NetworkCoordinator | None = None
    gateway: GatewayCoordinator | None = None
    clients: ClientCoordinator | None = None
    client_probes: ClientProbeCoordinator | None = None
    work_probes: WorkProbeCoordinator | None = None
    lady: LadyRuntime | None = None
    wifi: WifiCoordinator | None = None
    frigate_actor: FrigateActorCoordinator | None = None
    ipv6_actor: IPv6Coordinator | None = None


type RouteConfigEntry = ConfigEntry[RuntimeData]
