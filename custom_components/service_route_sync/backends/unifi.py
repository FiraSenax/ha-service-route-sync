"""Reuse the already authenticated official HA UniFi integration."""

from copy import deepcopy
from ..core import SyncError


class UniFiBackend:
    def __init__(self, hass, parent_entry_id):
        self.hass = hass
        self.parent_entry_id = parent_entry_id

    def _api(self):
        entry = self.hass.config_entries.async_get_entry(self.parent_entry_id)
        hub = getattr(entry, "runtime_data", None) if entry else None
        if entry is None or entry.domain != "unifi" or hub is None or not getattr(hub, "api", None):
            raise SyncError("The selected UniFi integration is not loaded")
        return hub.api

    async def read_routes(self):
        from aiounifi.models.traffic_route import TrafficRouteListRequest

        response = await self._api().request(TrafficRouteListRequest.create())
        rows = response.get("data") if isinstance(response, dict) else response
        if not isinstance(rows, list) or any(
            not isinstance(x, dict) or not x.get("_id") for x in rows
        ):
            raise SyncError("Unexpected UniFi route response")
        return {str(x["_id"]): deepcopy(x) for x in rows}

    async def write_route(self, route):
        from aiounifi.models.traffic_route import TrafficRouteSaveRequest

        await self._api().request(TrafficRouteSaveRequest.create(deepcopy(route)))
