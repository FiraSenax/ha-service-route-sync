"""Separate Lady LAN/routing source and independent WAN/LAN composition."""

import asyncio
import json

from homeassistant.util import dt as dt_util
from homeassistant.exceptions import HomeAssistantError

from .backends.gateway_ssh import GatewaySSHConfig, GatewayTransportError, read_gateway
from .client_probe_protocol import parse_client_probes
from .client_observation import ClientDataError
from .client_metrics import fresh
from .lady_coordinator import TimedObservation
from .lady_dsl import DSLDataError
from .network import NetworkDataError


class LadyClientSSHConfig(GatewaySSHConfig):
    def arguments(self):
        return (*super().arguments()[:-1], "service-route-sync lady-client v1")


class LadyPCProbeReader:
    def __init__(self, hass, config, clients):
        if tuple(clients.profiles) != ("ladyofgames",):
            raise ClientDataError("Lady probes require a separate single-client scope")
        self.hass, self.config, self.clients = hass, config, clients

    async def __call__(self):
        await self.hass.async_add_executor_job(self.config.validate_files)
        data = self.clients.data
        now = dt_util.utcnow()
        valid = (
            self.clients.last_update_success
            and data is not None
            and fresh(data.observed_at, now, 45)
        )
        client = data.clients.get("ladyofgames") if valid else None
        address = client.ipv4_address if client is not None and client.online else None
        frame = json.dumps(
            {"schema": 1, "addresses": {"ladyofgames": address}}, separators=(",", ":")
        ).encode()
        raw = await read_gateway(self.config, timeout=20, input_data=frame)
        now = dt_util.utcnow()
        snapshot = parse_client_probes(raw, now, ("ladyofgames",))
        result = {}
        probe = snapshot.probes.get("ladyofgames")
        if probe is not None and fresh(probe.observed_at, now, 45):
            result["lan"] = {
                "loss": probe.loss,
                "avg": probe.average_ms,
                "max": probe.maximum_ms,
                "rc": 1 if probe.loss == 100 else 0,
            }
        route = snapshot.routes.get("ladyofgames")
        if route is not None and route.valid and fresh(route.observed_at, now, 45):
            result["routing"] = {"rc": 0, "counts": dict(route.connections)}
        return TimedObservation(result, snapshot.observed_at.timestamp())


class LadyProbeReader:
    def __init__(self, dsl, pc):
        self.dsl, self.pc = dsl, pc

    async def __call__(self):
        async def isolated(reader):
            try:
                return await reader()
            except (
                HomeAssistantError,
                GatewayTransportError,
                ClientDataError,
                DSLDataError,
                NetworkDataError,
                OSError,
                TimeoutError,
            ):
                return None

        values = await asyncio.gather(isolated(self.dsl), isolated(self.pc))
        now = dt_util.utcnow().timestamp()
        result, stamps = {}, []
        for value, keys in zip(values, ({"wan_cf", "wan_google"}, {"lan", "routing"}), strict=True):
            if isinstance(value, TimedObservation) and 0 <= now - value.observed_at <= 45:
                result.update({key: row for key, row in value.value.items() if key in keys})
                stamps.append(value.observed_at)
        if not stamps:
            raise HomeAssistantError("Lady gateway probe sources unavailable")
        return TimedObservation(result, min(stamps))
