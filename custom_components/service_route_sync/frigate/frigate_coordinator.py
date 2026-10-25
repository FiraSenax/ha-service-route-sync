"""Independent read-only status lifecycle and explicitly gated recovery bridge."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from pathlib import Path
import re

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ..backends.frigate_ssh import (
    FrigateStatusSSHConfig,
    FrigateRestartSSHConfig,
    read_frigate_status,
    request_frigate_restart,
)
from ..backends.gateway_ssh import GatewayTransportError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FrigateObservation:
    observed_at: datetime
    running: bool
    started_at: datetime | None


class FrigateActorCoordinator(DataUpdateCoordinator[FrigateObservation]):
    def __init__(self, hass, entry):
        self.entry = entry
        settings = self.settings
        identifier = settings.get("frigate_actor_key_id")
        if not isinstance(identifier, str) or not re.fullmatch("[0-9a-f]{32}", identifier):
            raise ValueError("Invalid Frigate credential scope")
        self.binding = (settings.get("frigate_actor_host"), identifier)
        directory = Path(hass.config.path("frigate-restart-credentials", identifier))
        self.status_config = FrigateStatusSSHConfig(self.binding[0], directory)
        self.restart_config = FrigateRestartSSHConfig(self.binding[0], directory)
        self.restart_lock = asyncio.Lock()
        self.closed = False
        super().__init__(
            hass,
            _LOGGER,
            name="Service Route Sync Frigate status",
            config_entry=entry,
            update_interval=timedelta(seconds=30),
        )

    @property
    def settings(self):
        return {**self.entry.data, **self.entry.options}

    @property
    def binding_current(self):
        if self.closed or self.hass.is_stopping:
            return False
        settings = self.settings
        return settings.get("frigate_actor_enabled") is True and self.binding == (
            settings.get("frigate_actor_host"),
            settings.get("frigate_actor_key_id"),
        )

    @property
    def restart_enabled(self):
        return self.binding_current and self.settings.get("frigate_restart_enabled") is True

    async def _async_update_data(self):
        try:
            if not self.binding_current:
                raise GatewayTransportError("Frigate configuration changed")
            await self.hass.async_add_executor_job(self.status_config.validate_files)
            if not self.binding_current:
                raise GatewayTransportError("Frigate configuration changed")
            value = await read_frigate_status(self.status_config)
            if not self.binding_current:
                raise GatewayTransportError("Frigate configuration changed")
            started = datetime.fromisoformat(value["started"].replace("Z", "+00:00"))
            return FrigateObservation(
                dt_util.utcnow(), value["running"], started if started.year >= 1970 else None
            )
        except (GatewayTransportError, OSError, ValueError):
            raise UpdateFailed("Unable to obtain trusted Frigate status") from None

    async def async_request_restart(self):
        if self.restart_lock.locked():
            raise GatewayTransportError("Frigate request is already running")
        async with self.restart_lock:
            if not self.restart_enabled:
                raise GatewayTransportError("Frigate recovery is not enabled")
            await self.hass.async_add_executor_job(self.restart_config.validate_files)
            # Validation yields to the event loop; disabling/rebinding while it
            # runs must prevent using the old peer or credential scope.
            if not self.restart_enabled:
                raise GatewayTransportError("Frigate recovery configuration changed")
            value = await request_frigate_restart(self.restart_config)
            return {
                key: value[key] for key in ("restart_requested", "attempt_epoch", "container_id")
            }

    async def async_shutdown(self):
        self.closed = True
        await super().async_shutdown()
