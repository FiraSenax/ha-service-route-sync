"""UniFi access using credentials explicitly supplied to this integration."""

from copy import deepcopy
from dataclasses import dataclass
import ipaddress
from json import JSONDecodeError
import logging
import re

from aiohttp import ClientError, ClientSession, ClientTimeout, Fingerprint

from ..core import SyncError
from ..client_observation import canonical_mac

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApiRequest:
    """A fixed controller operation; never supplied by service callers."""

    method: str
    path: str
    data: dict | None = None


class UniFiBackend:
    async def _read_identity_records(self, request):
        """Keep raw controller records private; callers publish parsed observations."""
        response = await self._request(request)
        rows = response.get("data") if isinstance(response, dict) else None
        if not isinstance(rows, list):
            raise SyncError("Unexpected UniFi observation response")
        identities = set()
        for row in rows:
            identity = canonical_mac(row.get("mac")) if isinstance(row, dict) else None
            if identity is None or identity in identities:
                raise SyncError("Ambiguous UniFi observation identity")
            identities.add(identity)
        return deepcopy(rows)

    async def read_clients(self):
        """Read only active clients using a fixed API path."""
        return await self._read_identity_records(ApiRequest("get", "/stat/sta"))

    async def read_devices(self):
        """Read switch port records without accepting a host, path or credentials."""
        return await self._read_identity_records(ApiRequest("get", "/stat/device"))

    def __init__(self, hass, settings):
        self.settings = dict(settings)
        self.tls_pin_sha256 = settings.get("unifi_tls_pin_sha256") or None
        self.route_api_key = settings.get("unifi_route_api_key") or None
        self.api_key = settings.get("unifi_api_key")
        self.host = settings.get("unifi_host")
        self.port = settings.get("unifi_port", 443)
        self.site = settings.get("unifi_site", "default")
        if not isinstance(self.host, str) or not self.host:
            raise SyncError("Configure this integration's UniFi endpoint and credentials")
        try:
            ipaddress.ip_address(self.host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", self.host):
                raise SyncError("Invalid UniFi host") from None
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise SyncError("Invalid UniFi port")
        if not isinstance(self.site, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.site):
            raise SyncError("Invalid UniFi site")
        if self.tls_pin_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.tls_pin_sha256
        ):
            raise SyncError("Invalid UniFi certificate pin")
        if self.route_api_key is not None and (
            not isinstance(self.route_api_key, str)
            or not 16 <= len(self.route_api_key) <= 512
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in self.route_api_key)
        ):
            raise SyncError("Invalid dedicated UniFi route API key")
        if (
            not isinstance(self.api_key, str)
            or not 16 <= len(self.api_key) <= 512
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in self.api_key)
        ):
            raise SyncError("Configure this integration's own UniFi local API key")
        self._pinned_session = None

    async def async_close(self):
        if self._pinned_session is not None:
            await self._pinned_session.close()
            self._pinned_session = None

    async def _request(self, request):
        """Keep API error bodies out of HA logs and diagnostics.

        Authentication is owned exclusively by this config entry.
        Cancellation is deliberately not caught.
        """
        try:
            if request.path.startswith("/trafficroutes"):
                return await self._route_key_request(request)
            if request.method != "get" or request.path not in ("/stat/sta", "/stat/device"):
                raise SyncError("Unexpected UniFi observation operation")
            return await self._key_request(request)
        except (ClientError, JSONDecodeError, TimeoutError, OSError) as exc:
            _LOGGER.warning("UniFi API request failed (%s)", type(exc).__name__)
            raise SyncError("The selected UniFi API request failed") from None

    async def _route_key_request(self, request):
        """Use a dedicated key only for fixed traffic-route paths, pinned end to end."""
        if request.method not in ("get", "put"):
            raise SyncError("Verified TLS and a fixed route operation are required")
        if request.method == "get" and request.path != "/trafficroutes":
            raise SyncError("Unexpected UniFi route path")
        if request.method == "put" and not re.fullmatch(
            r"/trafficroutes/[0-9a-f-]{36}", request.path
        ):
            raise SyncError("Unexpected UniFi route path")
        return await self._key_request(request)

    async def _key_request(self, request):
        route = request.path.startswith("/trafficroutes")
        if self._pinned_session is None:
            self._pinned_session = ClientSession(trust_env=False)
        authority = f"[{self.host}]" if ":" in self.host else self.host
        prefix = (
            f"/proxy/network/v2/api/site/{self.site}"
            if route
            else f"/proxy/network/api/s/{self.site}"
        )
        key = (self.route_api_key or self.api_key) if route else self.api_key
        url = f"https://{authority}:{self.port}{prefix}{request.path}"
        async with self._pinned_session.request(
            request.method.upper(),
            url,
            headers={"X-API-KEY": key},
            json=deepcopy(request.data) if request.method == "put" else None,
            ssl=Fingerprint(bytes.fromhex(self.tls_pin_sha256)) if self.tls_pin_sha256 else True,
            timeout=ClientTimeout(total=15),
            allow_redirects=False,
        ) as response:
            if response.status not in (200, 201, 204):
                _LOGGER.warning("UniFi API rejected request (HTTP %s)", response.status)
                raise SyncError("The configured UniFi API request failed")
            if request.method == "put":
                return None
            return await response.json(content_type=None)

    async def read_routes(self):
        response = await self._request(ApiRequest("get", "/trafficroutes"))
        rows = response.get("data") if isinstance(response, dict) else response
        if not isinstance(rows, list):
            raise SyncError("Unexpected UniFi route response")
        routes = {}
        for row in rows:
            if not isinstance(row, dict):
                raise SyncError("Unexpected UniFi route response")
            route_id = row.get("_id")
            if not isinstance(route_id, str) or not route_id or route_id in routes:
                raise SyncError("Ambiguous UniFi route identity")
            routes[route_id] = deepcopy(row)
        return routes

    async def write_route(self, route):
        if self.tls_pin_sha256 is None or self.route_api_key is None:
            raise SyncError(
                "A pinned certificate and dedicated API key are required for route writes"
            )
        await self._request(ApiRequest("put", f"/trafficroutes/{route['_id']}", deepcopy(route)))

    async def read_gateways(self) -> dict[str, dict]:
        """Read WAN-capable devices with this entry's credentials."""
        response = await self._request(ApiRequest("get", "/stat/device"))
        rows = response.get("data") if isinstance(response, dict) else None
        if not isinstance(rows, list):
            raise SyncError("Unexpected UniFi device response")
        gateways = {}
        for row in rows:
            if not isinstance(row, dict):
                raise SyncError("Unexpected UniFi device record")
            if "wan1" not in row and "wan2" not in row:
                continue
            device_id = row.get("_id")
            if not isinstance(device_id, str) or not device_id or device_id in gateways:
                raise SyncError("Ambiguous UniFi gateway identity")
            gateways[device_id] = deepcopy(row)
        return gateways


async def read_controller(hass, settings, method, *, backend_factory=UniFiBackend):
    """Flow probes always close their temporary authenticated session."""
    backend = backend_factory(hass, settings)
    try:
        return await getattr(backend, method)()
    finally:
        await backend.async_close()
