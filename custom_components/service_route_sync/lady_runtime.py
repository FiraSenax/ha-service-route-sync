"""Explicit Lady feature assembly; observation mode cannot send notifications."""

from dataclasses import dataclass
import time

from homeassistant.exceptions import ConfigEntryNotReady

from .backends.dsl_ssh import DSLSSHConfig
from .backends.windows_ssh import WindowsArchiveSSHConfig, read_windows_archive
from .client_coordinator import ClientCoordinator, validate_profiles
from .gateway_credentials import credential_directory, same_pinned_gateway
from .lady_client import LadyClientReader
from .lady_coordinator import LadyCoordinator
from .lady_dns import LadyDNSReader
from .lady_dsl import LadyDSLReader
from .lady_notifier import LadyNotifier
from .lady_probes import LadyClientSSHConfig, LadyPCProbeReader, LadyProbeReader
from .lady_store import LadyCheckpointStore
from .lady_transaction import LadyTransaction
from .lady_history import LadyHistory, journal_path


class ObservationOnlyNotifier:
    async def async_drain(self):
        return 0


@dataclass
class LadyRuntime:
    clients: ClientCoordinator
    incidents: LadyCoordinator
    active: bool


async def prepare_lady(hass, entry, settings):
    profile = settings.get("lady_profile")
    if tuple(validate_profiles([profile])) != ("ladyofgames",):
        raise ValueError("Lady requires exactly its separately configured profile")
    gaming = settings.get("client_profiles", [])
    if gaming:
        assigned = validate_profiles(gaming)
        if "ladyofgames" in assigned or any(set(profile["macs"]).intersection(macs) for macs in assigned.values()):
            raise ValueError("Lady and Gaming client scopes must remain separate")
    active = settings.get("lady_active", False)
    if type(active) is not bool:
        raise ValueError("Invalid Lady operating mode")
    gateway = settings.get("lady_gateway_host")
    dsl = DSLSSHConfig(gateway, credential_directory(hass, settings.get("lady_dsl_key_id")))
    pc = LadyClientSSHConfig(gateway, credential_directory(hass, settings.get("lady_pc_key_id")))
    windows = WindowsArchiveSSHConfig(settings.get("lady_windows_host"),
                                      credential_directory(hass, settings.get("lady_windows_key_id")))
    if len({dsl.credential_directory, pc.credential_directory, windows.credential_directory}) != 3:
        raise ValueError("Lady source credentials must have separate scopes")
    for config in (dsl, pc, windows):
        await hass.async_add_executor_job(config.validate_files)
    if not await hass.async_add_executor_job(same_pinned_gateway, dsl.known_hosts, pc.known_hosts):
        raise ValueError("Lady router source host pins disagree")
    # Keep observation checkpoints separate from the final imported live state.
    store = LadyCheckpointStore(hass, entry.entry_id + ("_shadow" if not active else ""))
    await store.async_load()
    if active and (store.state is None or not isinstance(store.state["cursor"], dict) or "day" not in store.state["cursor"]):
        raise ConfigEntryNotReady("Lady requires a verified imported daily checkpoint")
    history = LadyHistory(journal_path(hass.config.path("service_route_sync", "lady_history"),
                                      store.store.key, (store.state or {}).get("history_epoch")))
    if active:
        marker = store.state.get("history_revision", 0)
        if not marker or not store.state.get("history_epoch"):
            raise ConfigEntryNotReady("Lady requires an imported history journal")
        await hass.async_add_executor_job(history.snapshot, marker, time.time())
    transaction = LadyTransaction(store, history)
    clients = ClientCoordinator(hass, entry, [profile])
    async def read_windows(cursor):
        return await read_windows_archive(windows, cursor)
    readers = {
        "api": LadyClientReader(clients, "ladyofgames"),
        "probe": LadyProbeReader(LadyDSLReader(dsl), LadyPCProbeReader(hass, pc, clients)),
        "dns": LadyDNSReader(), "windows": read_windows,
    }
    notifier = LadyNotifier(hass, transaction) if active else ObservationOnlyNotifier()
    incidents = LadyCoordinator(hass, entry, transaction, notifier, readers,
                                publish_heartbeat=active)
    return LadyRuntime(clients, incidents, active)
