"""Diagnostics intentionally omit credentials, topology and destination lists."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "services": coordinator.settings["services"],
        "automatic_apply": coordinator.settings.get("automatic_apply", False),
        "write_blocked": coordinator.write_blocked,
        "backup_count": len(coordinator.backups),
        "status": coordinator.data.get("status"),
        "prefix_count": coordinator.data.get("prefix_count"),
        "pending_routes": coordinator.data.get("pending_routes"),
    }
