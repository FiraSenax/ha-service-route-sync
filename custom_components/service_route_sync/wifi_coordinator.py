"""All-client wireless observations through the native authenticated parent."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import hashlib
from pathlib import Path

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .backends.unifi import UniFiBackend
from .core import SyncError
from .wifi_observation import WirelessObservation, WifiDataError, observe_wireless
from .wifi_history import WifiHistory, WifiHistoryError

_LOGGER = logging.getLogger(__name__)


def prepare_wifi(hass, entry, settings):
    """Build an entry-owned journal; opening and writes remain off the event loop."""
    identity = hashlib.sha256(entry.entry_id.encode()).hexdigest()
    history = WifiHistory(Path(hass.config.path('.storage', 'service_route_sync_wifi', identity + '.sqlite')))
    return WifiCoordinator(hass, entry, settings['unifi_entry_id'], history)


@dataclass(frozen=True, slots=True)
class WirelessSnapshot:
    observed_at: datetime
    clients: tuple[WirelessObservation, ...]


class WifiCoordinator(DataUpdateCoordinator[WirelessSnapshot]):
    def __init__(self, hass, entry, parent_entry_id, history=None):
        self.backend = UniFiBackend(hass, parent_entry_id)
        self.history = history
        super().__init__(hass, _LOGGER, name="Service Route Sync wireless clients", config_entry=entry,
                         update_interval=timedelta(seconds=30))

    async def _async_update_data(self):
        try:
            async with asyncio.timeout(8):
                rows = await self.backend.read_clients()
            now = dt_util.utcnow()
            clients = observe_wireless(rows, now)
            if self.history is not None:
                await self.hass.async_add_executor_job(self.history.record, now.timestamp(), clients)
            return WirelessSnapshot(now, clients)
        except (SyncError, WifiDataError, WifiHistoryError, OSError, TimeoutError):
            raise UpdateFailed("Unable to obtain complete wireless observations") from None
