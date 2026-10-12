"""Independent bounded public endpoint observations from the HA host."""

import asyncio
from datetime import timedelta
import logging
from types import MappingProxyType

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .work_probes import TARGETS, probe_work_endpoint

_LOGGER = logging.getLogger(__name__)


class WorkProbeCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        self.entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name="Service Route Sync work endpoints",
            config_entry=entry,
            update_interval=timedelta(seconds=60),
        )

    async def _async_update_data(self):
        targets = tuple(TARGETS)
        # Each fixed request has its own deadline and cancellation cleanup.
        results = await asyncio.gather(
            *(probe_work_endpoint(target, dt_util.utcnow) for target in targets)
        )
        return MappingProxyType(dict(zip(targets, results, strict=True)))
