"""Explicit Monitoring feature assembly; observation mode cannot send notifications."""

from dataclasses import dataclass

from .backends.dsl_ssh import DSLSSHConfig
from .backends.windows_ssh import WindowsArchiveSSHConfig, read_windows_archive
from .client_coordinator import ClientCoordinator, validate_profiles
from .gateway_credentials import credential_directory, same_pinned_gateway
from .monitoring_client import MonitoringClientReader
from .monitoring_coordinator import MonitoringCoordinator
from .monitoring_dns import MonitoringDNSReader, validate_dns_servers
from .monitoring_dsl import MonitoringDSLReader
from .monitoring_notifier import MonitoringNotifier
from .monitoring_probes import (
    MonitoringClientSSHConfig,
    MonitoringPCProbeReader,
    MonitoringProbeReader,
)
from .monitoring_store import MonitoringCheckpointStore
from .monitoring_transaction import MonitoringTransaction
from .monitoring_history import MonitoringHistory, journal_path
from .windows_source import WindowsBatch


class ObservationOnlyNotifier:
    async def async_drain(self):
        return 0


@dataclass
class MonitoringRuntime:
    clients: ClientCoordinator
    incidents: MonitoringCoordinator
    active: bool
    profile_name: str


async def prepare_monitoring(hass, entry, settings):
    profile = settings.get("monitoring_profile")
    if len(validate_profiles([profile])) != 1:
        raise ValueError("Monitoring requires exactly its separately configured profile")
    gaming = settings.get("client_profiles", [])
    if gaming:
        assigned = validate_profiles(gaming)
        if profile["id"] in assigned or any(
            set(profile["macs"]).intersection(macs) for macs in assigned.values()
        ):
            raise ValueError("Monitoring and Gaming client scopes must remain separate")
    active = settings.get("monitoring_active", False)
    if type(active) is not bool:
        raise ValueError("Invalid Monitoring operating mode")
    gateway = settings.get("monitoring_gateway_host")
    dsl = DSLSSHConfig(gateway, credential_directory(hass, settings.get("monitoring_dsl_key_id")))
    pc = MonitoringClientSSHConfig(
        gateway, credential_directory(hass, settings.get("monitoring_pc_key_id"))
    )
    windows = None
    configs = [dsl, pc]
    if settings.get("monitoring_windows_enabled"):
        windows = WindowsArchiveSSHConfig(
            settings.get("monitoring_windows_host"),
            credential_directory(hass, settings.get("monitoring_windows_key_id")),
        )
        configs.append(windows)
    if len({config.credential_directory for config in configs}) != len(configs):
        raise ValueError("Observation sources require separate credentials")
    for config in configs:
        await hass.async_add_executor_job(config.validate_files)
    if not await hass.async_add_executor_job(same_pinned_gateway, dsl.known_hosts, pc.known_hosts):
        raise ValueError("Monitoring router source host pins disagree")
    store = MonitoringCheckpointStore(hass, entry.entry_id)
    await store.async_load()
    history = MonitoringHistory(
        journal_path(
            hass.config.path("service_route_sync", "monitoring_history"),
            store.store.key,
            (store.state or {}).get("history_epoch"),
        )
    )
    transaction = MonitoringTransaction(
        store,
        history,
        notifications_enabled=active,
        windows_enabled=bool(settings.get("monitoring_windows_enabled")),
        engine_options={
            "expected_speed": settings.get("monitoring_expected_speed", 1000),
            "maintenance_hour": settings.get("monitoring_maintenance_hour", -1),
            "maintenance_minutes": settings.get("monitoring_maintenance_minutes", 2),
        },
    )
    clients = ClientCoordinator(hass, entry, [profile])

    async def read_windows(cursor):
        if windows is None:
            return WindowsBatch(None, (), None, False, False, 0, False)
        return await read_windows_archive(windows, cursor)

    readers = {
        "api": MonitoringClientReader(clients, profile["id"]),
        "probe": MonitoringProbeReader(
            MonitoringDSLReader(dsl), MonitoringPCProbeReader(hass, pc, clients)
        ),
        "dns": MonitoringDNSReader(validate_dns_servers(settings.get("monitoring_dns_servers"))),
        "windows": read_windows,
    }
    notifier = (
        MonitoringNotifier(
            hass,
            transaction,
            settings.get("monitoring_notify_service"),
            profile.get("name", profile["id"]),
        )
        if active
        else ObservationOnlyNotifier()
    )
    incidents = MonitoringCoordinator(
        hass, entry, transaction, notifier, readers, publish_heartbeat=active
    )
    return MonitoringRuntime(clients, incidents, active, profile.get("name", profile["id"]))
