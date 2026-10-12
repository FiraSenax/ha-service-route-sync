"""Independent read-only protocol observations with strict freshness and trust."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .backends.gateway_ssh import GatewaySSHConfig, GatewayTransportError, read_gateway
from .gateway_credentials import credential_directory
from .gateway_observation import GatewayObservation, parse_gateway_observation
from .network import NetworkDataError
from .runtime import RouteConfigEntry

_LOGGER = logging.getLogger(__name__)


class GatewayCoordinator(DataUpdateCoordinator[GatewayObservation]):
    """This feature cannot execute routing commands or return raw gateway data."""

    def __init__(self, hass: HomeAssistant, entry: RouteConfigEntry) -> None:
        self.entry = entry
        self.settings = {**entry.data, **entry.options}
        self.transport = GatewaySSHConfig(
            self.settings["gateway_observer_host"],
            credential_directory(hass, self.settings["gateway_observer_key_id"]),
        )
        super().__init__(
            hass,
            _LOGGER,
            name="Service Route Sync protocol observations",
            config_entry=entry,
            update_interval=timedelta(seconds=10),
        )

    async def _async_update_data(self) -> GatewayObservation:
        try:
            async with asyncio.timeout(15):
                await self.hass.async_add_executor_job(self.transport.validate_files)
                raw = await read_gateway(self.transport)
            return parse_gateway_observation(
                raw,
                dt_util.utcnow(),
                primary_interface=self.settings["gateway_primary_interface"],
                secondary_interface=self.settings["gateway_secondary_interface"],
            )
        except (OSError, GatewayTransportError, NetworkDataError, TimeoutError):
            # Neither local paths nor SSH/UDAPI responses belong in log chains.
            raise UpdateFailed("Unable to obtain verified gateway observations") from None
