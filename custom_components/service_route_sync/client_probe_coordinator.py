"""Independent local probe schedule; UniFi collection cannot fabricate probe success."""

from datetime import timedelta
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .backends.client_probe_ssh import ClientProbeSSHConfig, read_client_probes
from .backends.gateway_ssh import GatewayTransportError
from .client_coordinator import validate_profiles
from .client_probe_protocol import ClientProbeSnapshot, parse_client_probes
from .gateway_credentials import credential_directory
from .network import NetworkDataError
from .client_metrics import fresh

_LOGGER = logging.getLogger(__name__)


class ClientProbeCoordinator(DataUpdateCoordinator[ClientProbeSnapshot]):
    def __init__(self, hass, entry, clients=None):
        self.entry = entry
        self.clients = clients
        settings = {**entry.data, **entry.options}
        self.profile_ids = tuple(validate_profiles(settings["client_profiles"]))
        self.transport = ClientProbeSSHConfig(
            settings["client_probe_host"],
            credential_directory(hass, settings["client_probe_key_id"]),
        )
        super().__init__(
            hass,
            _LOGGER,
            name="Service Route Sync client probes",
            config_entry=entry,
            update_interval=timedelta(seconds=5),
        )

    async def _async_update_data(self):
        try:
            await self.hass.async_add_executor_job(self.transport.validate_files)
            if self.clients is None:
                raw = await read_client_probes(self.transport)
            else:
                data = self.clients.data
                valid = (
                    self.clients.last_update_success
                    and data is not None
                    and fresh(data.observed_at, dt_util.utcnow(), 75)
                )
                addresses = {
                    key: data.clients[key].ipv4_address
                    if valid and data.clients[key].online
                    else None
                    for key in self.profile_ids
                }
                raw = await read_client_probes(self.transport, addresses=addresses)
            return parse_client_probes(raw, dt_util.utcnow(), self.profile_ids)
        except (OSError, GatewayTransportError, NetworkDataError):
            raise UpdateFailed("Unable to obtain verified client probes") from None
