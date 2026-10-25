"""Bounded Prometheus exposition for the inventoried network metric contract.

No I/O, credentials, dynamic metric names or implicit missing-to-zero coercion.
"""

from __future__ import annotations

import math
from types import MappingProxyType


class MetricDataError(ValueError):
    """A sample cannot be safely exposed; error messages contain no sample data."""


CONTRACT = MappingProxyType(
    {
        "gaming_client_info": ("ip", "pc"),
        "gaming_client_online": ("pc",),
        "gaming_configured": ("pc",),
        "gaming_connection_type": ("pc",),
        "gaming_connections": ("pc", "route"),
        "gaming_exporter_timestamp_seconds": (),
        "gaming_monitor_fresh": ("pc",),
        "gaming_pc_state": ("pc",),
        "gaming_port_info": ("name", "pc", "port"),
        "gaming_port_link_down_count": ("pc", "port"),
        "gaming_port_rx_bytes": ("pc", "port"),
        "gaming_port_rx_dropped": ("pc", "port"),
        "gaming_port_rx_errors": ("pc", "port"),
        "gaming_port_speed": ("pc", "port"),
        "gaming_port_tx_bytes": ("pc", "port"),
        "gaming_port_tx_dropped": ("pc", "port"),
        "gaming_port_tx_errors": ("pc", "port"),
        "gaming_port_up": ("pc", "port"),
        "gaming_probe_avg": ("pc",),
        "gaming_probe_loss": ("pc",),
        "gaming_probe_max": ("pc",),
        "gaming_routing_valid": ("pc",),
        "gaming_sample_timestamp_seconds": ("pc",),
        "gaming_source_ok": ("pc", "source"),
        "gaming_wifi_noise": ("pc",),
        "gaming_wifi_rx_rate": ("pc",),
        "gaming_wifi_signal": ("pc",),
        "gaming_wifi_tx_packets": ("pc",),
        "gaming_wifi_tx_rate": ("pc",),
        "gaming_wifi_tx_retries": ("pc",),
        "monitoring_active_incidents": (),
        "monitoring_bandwidth_source_ok": (),
        "monitoring_connections": ("route",),
        "monitoring_dns_ms": ("resolver",),
        "monitoring_dns_ok": ("resolver",),
        "monitoring_dsl_mbps": ("direction",),
        "monitoring_event_timestamp_seconds": ("category", "event", "message", "status"),
        "monitoring_exporter_build_info": ("version",),
        "monitoring_exporter_collection_ok": (),
        "monitoring_incident_active": ("category",),
        "monitoring_maintenance": (),
        "monitoring_monitor_fresh": (),
        "monitoring_monitor_last_sample_timestamp_seconds": (),
        "monitoring_pc_expected_off": (),
        "monitoring_pc_state": (),
        "monitoring_pending_notifications": (),
        "monitoring_port_info": ("port",),
        "monitoring_port_link_down_count": ("port",),
        "monitoring_port_rx_bytes": ("port",),
        "monitoring_port_rx_dropped": ("port",),
        "monitoring_port_rx_errors": ("port",),
        "monitoring_port_speed": ("port",),
        "monitoring_port_tx_bytes": ("port",),
        "monitoring_port_tx_dropped": ("port",),
        "monitoring_port_tx_errors": ("port",),
        "monitoring_port_up": ("port",),
        "monitoring_probe_loss_percent": ("target",),
        "monitoring_probe_path_ok": ("target",),
        "monitoring_probe_rtt_ms": ("stat", "target"),
        "monitoring_recorded_events": ("category",),
        "monitoring_routing_valid": (),
        "monitoring_source_age_seconds": ("source",),
        "monitoring_source_ok": ("source",),
        "monitoring_windows_agent_age_seconds": (),
        "work_endpoint_dns_seconds": ("host", "target", "vantage"),
        "work_endpoint_fresh": ("host", "target", "vantage"),
        "work_endpoint_http_ok": ("host", "target", "vantage"),
        "work_endpoint_http_status": ("host", "target", "vantage"),
        "work_endpoint_tcp_seconds": ("host", "target", "vantage"),
        "work_endpoint_timestamp_seconds": ("host", "target", "vantage"),
        "work_endpoint_tls_seconds": ("host", "target", "vantage"),
        "work_endpoint_total_seconds": ("host", "target", "vantage"),
        "work_endpoint_transport_ok": ("host", "target", "vantage"),
        "work_endpoint_ttfb_seconds": ("host", "target", "vantage"),
    }
)
BINARY = frozenset(
    {
        "gaming_configured",
        "gaming_source_ok",
        "gaming_monitor_fresh",
        "gaming_client_online",
        "gaming_port_up",
        "gaming_port_info",
        "gaming_client_info",
        "gaming_routing_valid",
        "work_endpoint_fresh",
        "work_endpoint_transport_ok",
        "work_endpoint_http_ok",
        "monitoring_monitor_fresh",
        "monitoring_exporter_build_info",
        "monitoring_pc_expected_off",
        "monitoring_incident_active",
        "monitoring_maintenance",
        "monitoring_source_ok",
        "monitoring_probe_path_ok",
        "monitoring_port_info",
        "monitoring_port_up",
        "monitoring_dns_ok",
        "monitoring_routing_valid",
        "monitoring_bandwidth_source_ok",
        "monitoring_exporter_collection_ok",
    }
)
SIGNED = frozenset(
    {
        "gaming_pc_state",
        "monitoring_pc_state",
        "gaming_connection_type",
        "gaming_wifi_signal",
        "gaming_wifi_noise",
    }
)


class MetricWriter:
    """One frozen response with bounded labels and no duplicate time series."""

    def __init__(self):
        self._lines = []
        self._series = set()
        self._bytes = 0

    def add(self, name: str, value: int | float | bool | None, /, **labels: str) -> None:
        if name not in CONTRACT or set(labels) != set(CONTRACT[name]):
            raise MetricDataError("Unexpected network metric or label schema")
        if value is None:
            return
        if isinstance(value, bool) and name not in BINARY:
            raise MetricDataError("Boolean is not a numeric measurement")
        if not isinstance(value, (int, float)):
            raise MetricDataError("Metric must be a JSON number")
        try:
            number = float(value)
        except OverflowError:
            raise MetricDataError("Metric must be finite") from None
        if not math.isfinite(number) or (name not in SIGNED and number < 0):
            raise MetricDataError("Metric outside supported numeric range")
        if name in BINARY and number not in (0, 1):
            raise MetricDataError("Binary metric outside supported range")
        if name in ("gaming_pc_state", "monitoring_pc_state") and number not in (-1, 0, 1, 2):
            raise MetricDataError("PC state outside supported range")
        if name == "gaming_connection_type" and number not in (-1, 0, 1):
            raise MetricDataError("Connection state outside supported range")
        pairs = []
        for key, label in sorted(labels.items()):
            if not isinstance(label, str):
                raise MetricDataError("Labels must be text")
            try:
                size = len(label.encode("utf-8"))
            except UnicodeEncodeError:
                raise MetricDataError("Label must be valid UTF-8") from None
            if size > 2048 or any(ord(char) < 32 and char not in ("\n", "\t") for char in label):
                raise MetricDataError("Label outside supported text bounds")
            pairs.append((key, label))
        identity = (name, tuple(pairs))
        if identity in self._series:
            raise MetricDataError("Duplicate network time series")
        escaped = []
        for key, label in pairs:
            text = label.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
            escaped.append(key + '="' + text + '"')
        suffix = "{" + ",".join(escaped) + "}" if escaped else ""
        line = name + suffix + " " + str(number) + "\n"
        size = len(line.encode("utf-8"))
        if len(self._lines) >= 8192 or self._bytes + size > 2_000_000:
            raise MetricDataError("Network exposition exceeds response bounds")
        self._series.add(identity)
        self._lines.append(line)
        self._bytes += size

    def render(self) -> str:
        return "".join(self._lines)
