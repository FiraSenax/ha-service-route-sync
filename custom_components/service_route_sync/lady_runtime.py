"""Explicit Lady feature assembly; observation mode cannot send notifications."""

from dataclasses import dataclass

from .backends.dsl_ssh import DSLSSHConfig
from .backends.windows_ssh import WindowsArchiveSSHConfig, read_windows_archive
from .client_coordinator import ClientCoordinator, validate_profiles
from .gateway_credentials import credential_directory, same_pinned_gateway
from .lady_client import LadyClientReader
from .lady_coordinator import LadyCoordinator
from .lady_dns import LadyDNSReader, validate_dns_servers
from .lady_dsl import LadyDSLReader
from .lady_notifier import LadyNotifier
from .lady_probes import LadyClientSSHConfig, LadyPCProbeReader, LadyProbeReader
from .lady_store import LadyCheckpointStore
from .lady_transaction import LadyTransaction
from .lady_history import LadyHistory, journal_path
from .windows_source import WindowsBatch


class ObservationOnlyNotifier:
    async def async_drain(self):
        return 0


@dataclass
class LadyRuntime:
    clients: ClientCoordinator
    incidents: LadyCoordinator
    active: bool
    profile_name: str


async def prepare_lady(hass, entry, settings):
    profile = settings.get("lady_profile")
    if len(validate_profiles([profile])) != 1:
        raise ValueError("Lady requires exactly its separately configured profile")
    gaming = settings.get("client_profiles", [])
    if gaming:
        assigned = validate_profiles(gaming)
        if profile["id"] in assigned or any(
            set(profile["macs"]).intersection(macs) for macs in assigned.values()
        ):
            raise ValueError("Lady and Gaming client scopes must remain separate")
    active = settings.get("lady_active", False)
    if type(active) is not bool:
        raise ValueError("Invalid Lady operating mode")
    gateway = settings.get("lady_gateway_host")
    dsl = DSLSSHConfig(gateway, credential_directory(hass, settings.get("lady_dsl_key_id")))
    pc = LadyClientSSHConfig(gateway, credential_directory(hass, settings.get("lady_pc_key_id")))
    windows = None
    configs = [dsl, pc]
    if settings.get("lady_windows_enabled"):
        windows = WindowsArchiveSSHConfig(
            settings.get("lady_windows_host"),
            credential_directory(hass, settings.get("lady_windows_key_id")),
        )
        configs.append(windows)
    if len({config.credential_directory for config in configs}) != len(configs):
        raise ValueError("Observation sources require separate credentials")
    for config in configs:
        await hass.async_add_executor_job(config.validate_files)
    if not await hass.async_add_executor_job(same_pinned_gateway, dsl.known_hosts, pc.known_hosts):
        raise ValueError("Lady router source host pins disagree")
    store = LadyCheckpointStore(hass, entry.entry_id)
    await store.async_load()
    history = LadyHistory(
        journal_path(
            hass.config.path("service_route_sync", "lady_history"),
            store.store.key,
            (store.state or {}).get("history_epoch"),
        )
    )
    transaction = LadyTransaction(
        store,
        history,
        notifications_enabled=active,
        windows_enabled=bool(settings.get("lady_windows_enabled")),
        engine_options={
            "expected_speed": settings.get("lady_expected_speed", 1000),
            "maintenance_hour": settings.get("lady_maintenance_hour", -1),
            "maintenance_minutes": settings.get("lady_maintenance_minutes", 2),
        },
    )
    clients = ClientCoordinator(hass, entry, [profile])

    async def read_windows(cursor):
        if windows is None:
            return WindowsBatch(None, (), None, False, False, 0, False)
        return await read_windows_archive(windows, cursor)

    readers = {
        "api": LadyClientReader(clients, profile["id"]),
        "probe": LadyProbeReader(LadyDSLReader(dsl), LadyPCProbeReader(hass, pc, clients)),
        "dns": LadyDNSReader(validate_dns_servers(settings.get("lady_dns_servers"))),
        "windows": read_windows,
    }
    notifier = (
        LadyNotifier(
            hass,
            transaction,
            settings.get("lady_notify_service"),
            profile.get("name", profile["id"]),
        )
        if active
        else ObservationOnlyNotifier()
    )
    incidents = LadyCoordinator(
        hass, entry, transaction, notifier, readers, publish_heartbeat=active
    )
    return LadyRuntime(clients, incidents, active, profile.get("name", profile["id"]))
