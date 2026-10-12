"""Render the legacy Gaming series from independently timestamped observations."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from types import MappingProxyType

from .network_metrics import MetricDataError, MetricWriter
from .network import finite_number

if TYPE_CHECKING:
    from .client_coordinator import ClientSnapshot


@dataclass(frozen=True, slots=True)
class ProbeObservation:
    observed_at: datetime
    loss: float | None
    average_ms: float | None
    maximum_ms: float | None

    def __post_init__(self):
        if self.observed_at.tzinfo is None:
            raise MetricDataError("Probe timestamp requires a timezone")
        for key in ("loss", "average_ms", "maximum_ms"):
            value = getattr(self, key)
            if value is not None and finite_number(value) is None:
                raise MetricDataError("Invalid probe measurement")
        if self.loss is not None and self.loss > 100:
            raise MetricDataError("Invalid probe loss percentage")


@dataclass(frozen=True, slots=True)
class RoutingObservation:
    observed_at: datetime
    valid: bool
    connections: Mapping[str, int]

    def __post_init__(self):
        if self.observed_at.tzinfo is None or type(self.valid) is not bool:
            raise MetricDataError("Invalid routing observation metadata")
        if not isinstance(self.connections, Mapping) or set(self.connections) - {
            "dsl",
            "starlink",
            "vpn1",
            "vpn2",
            "other",
        }:
            raise MetricDataError("Unexpected connection route")
        if any(type(value) is not int or value < 0 for value in self.connections.values()):
            raise MetricDataError("Invalid connection count")
        object.__setattr__(self, "connections", MappingProxyType(dict(self.connections)))


def fresh(stamp: datetime, now: datetime, maximum: int) -> bool:
    return bool(
        stamp.tzinfo is not None
        and now.tzinfo is not None
        and 0 <= (now - stamp).total_seconds() < maximum
    )


def render_clients(
    profile_ids: tuple[str, ...],
    snapshot: "ClientSnapshot | None",
    source_ok: bool,
    probes: Mapping[str, ProbeObservation],
    routes: Mapping[str, RoutingObservation],
    now: datetime,
    *,
    probe_cycle_at: datetime | None = None,
) -> str:
    """A controller success cannot fabricate ping, routing or monitor success."""
    writer = MetricWriter()
    writer.add("gaming_exporter_timestamp_seconds", now.timestamp())
    api_fresh = bool(source_ok and snapshot is not None and fresh(snapshot.observed_at, now, 75))
    for identity in profile_ids:
        labels = {"pc": identity}
        row = snapshot.clients.get(identity) if api_fresh else None
        probe = probes.get(identity)
        probe_fresh = bool(probe is not None and fresh(probe.observed_at, now, 20))
        cycle_fresh = fresh(probe_cycle_at, now, 20) if probe_cycle_at is not None else probe_fresh
        writer.add("gaming_configured", 1, **labels)
        writer.add("gaming_source_ok", row is not None, source="unifi", **labels)
        writer.add("gaming_source_ok", cycle_fresh, source="probe", **labels)
        if probe_cycle_at is not None or probe is not None:
            stamp = probe_cycle_at if probe_cycle_at is not None else probe.observed_at
            writer.add("gaming_sample_timestamp_seconds", stamp.timestamp(), **labels)
        writer.add("gaming_monitor_fresh", row is not None and cycle_fresh, **labels)
        state = -1
        if row is not None:
            if not row.online:
                state = 0
            elif probe_fresh and probe.loss is not None:
                state = 1 if probe.loss < 100 else 2
        writer.add("gaming_pc_state", state, **labels)
        if row is None:
            continue
        writer.add("gaming_client_online", row.online, **labels)
        writer.add(
            "gaming_connection_type", {"wired": 1, "wifi": 0}.get(row.connection, -1), **labels
        )
        if row.ipv4_address is not None:
            writer.add("gaming_client_info", 1, ip=row.ipv4_address, **labels)
        if row.port is not None:
            port_labels = {**labels, "port": row.port.identity}
            for key in (
                "up",
                "rx_errors",
                "tx_errors",
                "rx_dropped",
                "tx_dropped",
                "rx_bytes",
                "tx_bytes",
                "link_down_count",
            ):
                writer.add("gaming_port_" + key, getattr(row.port, key), **port_labels)
            writer.add("gaming_port_speed", row.port.speed_mbit_s, **port_labels)
            writer.add("gaming_port_info", 1, name=row.port.name, **port_labels)
        if not row.online:
            continue
        if probe_fresh:
            writer.add("gaming_probe_loss", probe.loss, **labels)
            writer.add("gaming_probe_avg", probe.average_ms, **labels)
            writer.add("gaming_probe_max", probe.maximum_ms, **labels)
        if row.wifi is not None:
            for key in ("signal", "noise", "tx_rate", "rx_rate", "tx_retries", "tx_packets"):
                writer.add("gaming_wifi_" + key, getattr(row.wifi, key), **labels)
        route = routes.get(identity)
        valid = bool(route is not None and route.valid and fresh(route.observed_at, now, 75))
        writer.add("gaming_routing_valid", valid, **labels)
        if valid:
            for key in ("dsl", "starlink", "vpn1", "vpn2", "other"):
                writer.add("gaming_connections", route.connections.get(key), route=key, **labels)
    return writer.render()
