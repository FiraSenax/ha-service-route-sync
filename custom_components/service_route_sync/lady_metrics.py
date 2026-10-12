"""Legacy Lady metric names, backed by accepted native observations/history."""

from datetime import datetime
from zoneinfo import ZoneInfo

from .network_metrics import MetricWriter


def fresh(stamp, now, bound):
    return stamp is not None and 0 <= now - stamp <= bound


def render_lady(snapshot, now, bandwidth=None):
    writer = MetricWriter()
    def metric(name, value, **labels):
        writer.add("lady_" + name, value, **labels)
    stamp = snapshot["observed_at"]
    current = fresh(stamp, now, 45)
    row, engine = snapshot["row"], snapshot["engine"]
    sources = snapshot["sources"]
    metric("exporter_build_info", 1, version="ha-native-1")
    metric("exporter_collection_ok", current)
    metric("monitor_last_sample_timestamp_seconds", stamp)
    metric("monitor_fresh", current)
    metric("pending_notifications", snapshot["pending"])
    off = engine.get("expected_power_off", False)
    loss = row.get("probes", {}).get("lan", {}).get("loss")
    state = -1 if not current or loss is None else 0 if off else 1 if loss < 100 else 2
    metric("pc_state", state)
    metric("pc_expected_off", off if current else None)
    incidents = engine.get("incidents", {})
    metric("active_incidents", sum(value["active"] for value in incidents.values()) if current else None)
    for category, value in incidents.items():
        metric("incident_active", value["active"] if current else None, category=category)
    local = datetime.fromtimestamp(now, ZoneInfo("Europe/Berlin"))
    metric("maintenance", local.hour == 5 and local.minute < 2)
    for name in ("api", "probe", "windows", "dns253", "dns254"):
        source = sources[name]
        maximum = 45 if name in ("api", "probe") else 100
        available = fresh(source["observed_at"], now, maximum)
        metric("source_ok", source["ok"] and available, source=name)
        metric("source_age_seconds", now - source["observed_at"] if available else None, source=name)
    if current:
        for target in ("lan", "wan_cf", "wan_google"):
            probe = row.get("probes", {}).get(target)
            if probe is not None:
                metric("probe_loss_percent", probe.get("loss"), target=target)
                metric("probe_rtt_ms", probe.get("avg"), stat="avg", target=target)
                metric("probe_rtt_ms", probe.get("max"), stat="max", target=target)
                metric("probe_path_ok", probe.get("loss") is not None, target=target)
        port = row.get("port")
        if port is not None:
            label = row["port_identity"]
            metric("port_info", 1, port=label)
            for name in ("up", "speed", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped", "link_down_count", "rx_bytes", "tx_bytes"):
                metric("port_" + name, port.get(name), port=label)
        for resolver, value in row.get("dns", {}).items():
            metric("dns_ok", value.get("ok"), resolver=resolver)
            metric("dns_ms", value.get("ms"), resolver=resolver)
        agent = row.get("windows", {}).get("agent")
        if agent is not None and 0 <= now - agent["last_keepalive"]:
            metric("windows_agent_age_seconds", now - agent["last_keepalive"])
    routing = row.get("probes", {}).get("routing")
    valid = bool(current and routing is not None and routing.get("rc") == 0)
    metric("routing_valid", valid)
    if valid:
        for route in ("dsl", "starlink", "vpn1", "vpn2", "other"):
            metric("connections", routing["counts"].get(route), route=route)
    bw_fresh = bandwidth is not None and fresh(bandwidth["observed_at"], now, 90)
    metric("bandwidth_source_ok", bw_fresh)
    if bw_fresh:
        for direction in ("upload", "download"):
            metric("dsl_mbps", bandwidth.get(direction), direction=direction)
    history = snapshot["history"]
    for category, count in history["counts"].items():
        metric("recorded_events", count, category=category)
    emitted = set()
    for event in history["recent"]:
        message = event["message"].replace("\r\n", "\n").replace("\r", "\n")
        message = "".join(char if ord(char) >= 32 or char in "\n\t" else "\ufffd" for char in message)[:500]
        labels = (event["key"], "Entwarnung" if event["recovery"] else "Hinweis", message, str(event["time"]))
        if labels in emitted:
            continue
        emitted.add(labels)
        metric("event_timestamp_seconds", event["time"], category=labels[0], status=labels[1], message=labels[2], event=labels[3])
    return writer.render()
