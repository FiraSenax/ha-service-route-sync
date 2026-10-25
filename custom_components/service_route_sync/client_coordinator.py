"""Read-only client collection through this integration’s own UniFi connection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import re
from types import MappingProxyType
from collections.abc import Mapping

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .backends.unifi import UniFiBackend
from .client_observation import ClientDataError, ClientObservation, canonical_mac, observe_client
from .core import SyncError
from .runtime import RouteConfigEntry

_LOGGER = logging.getLogger(__name__)
_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")


def validate_profiles(raw: object) -> Mapping[str, tuple[str, ...]]:
    """Freeze explicit MAC aliases; never infer identity from names or IPs."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= 64:
        raise ClientDataError("Expected a bounded client profile list")
    profiles = {}
    assigned = set()
    for row in raw:
        if not isinstance(row, dict) or set(row) - {"id", "name", "macs"}:
            raise ClientDataError("Unexpected client profile schema")
        identity, aliases = row.get("id"), row.get("macs")
        if "name" in row and (not isinstance(row["name"], str) or not 1 <= len(row["name"]) <= 128):
            raise ClientDataError("Invalid client display name")
        if not isinstance(identity, str) or not _ID.fullmatch(identity) or identity in profiles:
            raise ClientDataError("Invalid or duplicate client profile identity")
        if not isinstance(aliases, list) or not 1 <= len(aliases) <= 16:
            raise ClientDataError("Expected bounded explicit client aliases")
        macs = tuple(canonical_mac(value) for value in aliases)
        if None in macs or len(set(macs)) != len(macs) or assigned.intersection(macs):
            raise ClientDataError("Invalid or ambiguous client aliases")
        assigned.update(macs)
        profiles[identity] = macs
    return MappingProxyType(profiles)


@dataclass(frozen=True, slots=True)
class ClientSnapshot:
    observed_at: datetime
    clients: Mapping[str, ClientObservation]


class ClientCoordinator(DataUpdateCoordinator[ClientSnapshot]):
    """Publish only complete snapshots; a failed API call never implies offline."""

    def __init__(self, hass: HomeAssistant, entry: RouteConfigEntry, profiles: object) -> None:
        self.entry = entry
        self.profiles = validate_profiles(profiles)
        self.profile_names = MappingProxyType(
            {row["id"]: row.get("name", row["id"]) for row in profiles}
        )
        settings = {**entry.data, **entry.options}
        self.backend = UniFiBackend(hass, settings)
        super().__init__(
            hass,
            _LOGGER,
            name="Service Route Sync clients",
            config_entry=entry,
            update_interval=timedelta(seconds=30),
        )

    async def async_shutdown(self):
        await super().async_shutdown()
        await self.backend.async_close()

    async def _async_update_data(self) -> ClientSnapshot:
        try:
            async with asyncio.timeout(8):
                try:
                    async with asyncio.TaskGroup() as group:
                        clients_task = group.create_task(self.backend.read_clients())
                        devices_task = group.create_task(self.backend.read_devices())
                except* SyncError:
                    raise SyncError("Client observation request failed") from None
            now = dt_util.utcnow()
            observations = {
                identity: observe_client(macs, clients_task.result(), devices_task.result(), now)
                for identity, macs in self.profiles.items()
            }
            return ClientSnapshot(now, MappingProxyType(observations))
        except (SyncError, ClientDataError, TimeoutError):
            raise UpdateFailed("Unable to obtain valid client observations") from None
