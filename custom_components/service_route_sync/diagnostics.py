"""Diagnostics intentionally omit credentials, topology and destination lists."""


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    coordinator = runtime.routes
    report = {
        "routing_enabled": coordinator is not None,
        "network_enabled": runtime.network is not None,
        "gateway_observer_enabled": runtime.gateway is not None,
        "client_monitoring_enabled": runtime.clients is not None,
        "client_probe_enabled": runtime.client_probes is not None,
        "work_probe_enabled": runtime.work_probes is not None,
        "wifi_history_enabled": getattr(runtime, 'wifi', None) is not None,
    }
    if wifi := getattr(runtime, 'wifi', None):
        data = wifi.data
        report['wifi'] = {
            'last_update_success': wifi.last_update_success,
            'observed_at': data.observed_at.isoformat() if data else None,
            'client_count': len(data.clients) if data else None,
            'history_enabled': wifi.history is not None,
        }
    if frigate := getattr(runtime, 'frigate_actor', None):
        data = frigate.data
        report['frigate'] = {
            'last_update_success': frigate.last_update_success,
            'observed_at': data.observed_at.isoformat() if data else None,
            'running': data.running if data else None,
            'restart_enabled': frigate.restart_enabled,
        }
    if lady := getattr(runtime, "lady", None):
        data = lady.incidents.data
        report["lady"] = {
            "active": lady.active,
            "last_update_success": lady.incidents.last_update_success,
            "observed_at": data["observed_at"] if data else None,
            "sources": dict(data["sources"]) if data else None,
            "pending": data["pending"] if data else None,
            "notification_ok": data["notification_ok"] if data else None,
        }
    if runtime.clients is not None:
        data = runtime.clients.data
        report["clients"] = {
            "last_update_success": runtime.clients.last_update_success,
            "observed_at": data.observed_at.isoformat() if data else None,
            "configured_count": len(runtime.clients.profiles),
        }
    if runtime.client_probes is not None:
        data = runtime.client_probes.data
        report["client_probes"] = {
            "last_update_success": runtime.client_probes.last_update_success,
            "observed_at": data.observed_at.isoformat() if data else None,
            "verified_count": sum(data.identity_verified.values()) if data else 0,
        }
    if runtime.work_probes is not None:
        data = runtime.work_probes.data
        report["work_probes"] = {
            "last_update_success": runtime.work_probes.last_update_success,
            "target_count": len(data) if data else 0,
            "observed_at": min(row.observed_at for row in data.values()).isoformat()
            if data
            else None,
            "successful_transports": sum(row.transport_ok for row in data.values()) if data else 0,
        }
    if runtime.network is not None:
        data = runtime.network.data
        report["network"] = {
            "last_update_success": runtime.network.last_update_success,
            "observed_at": data.observed_at.isoformat() if data else None,
            "primary_present": data.primary is not None if data else False,
            "secondary_present": data.secondary is not None if data else False,
        }
    if coordinator is None:
        return report
    report.update(
        {
            "services": coordinator.settings["services"],
            "automatic_apply": coordinator.settings.get("automatic_apply", False),
            "write_blocked": coordinator.write_blocked,
            "backup_count": len(coordinator.backups),
            "status": (coordinator.data or {}).get("status"),
            "prefix_count": (coordinator.data or {}).get("prefix_count"),
            "pending_routes": (coordinator.data or {}).get("pending_routes"),
        }
    )
    return report
