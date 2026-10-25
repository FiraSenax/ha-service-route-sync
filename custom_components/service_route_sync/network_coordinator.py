"""Local WAN telemetry, independent of destination-feed scheduling."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .backends.unifi import UniFiBackend
from .core import SyncError
from .network import NetworkDataError, NetworkObservation, parse_gateway
from .runtime import RouteConfigEntry

_LOGGER = logging.getLogger(__name__)


class NetworkCoordinator(DataUpdateCoordinator[NetworkObservation]):
    """Fetch a fixed API path through the existing authenticated parent."""

    def __init__(self, hass: HomeAssistant, entry: RouteConfigEntry) -> None:
        self.entry = entry
        self.settings = {**entry.data, **entry.options}
        self.backend = UniFiBackend(hass, self.settings)
        super().__init__(
            hass,
            _LOGGER,
            name="Service Route Sync WAN",
            config_entry=entry,
            update_interval=timedelta(seconds=self.settings.get("network_interval_seconds", 10)),
        )

    async def async_shutdown(self):
        await super().async_shutdown()
        await self.backend.async_close()

    async def _async_update_data(self) -> NetworkObservation:
        try:
            async with asyncio.timeout(8):
                gateways = await self.backend.read_gateways()
            gateway = gateways.get(self.settings["network_gateway_id"])
            if gateway is None:
                raise UpdateFailed("The selected gateway is no longer available")
            if gateway.get("state") != 1:
                raise UpdateFailed("The selected gateway is not connected to its controller")
            return parse_gateway(gateway, dt_util.utcnow())
        except (SyncError, NetworkDataError, TimeoutError) as exc:
            raise UpdateFailed("Unable to obtain valid WAN observations") from exc
