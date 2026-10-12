"""Reuse the already authenticated official HA UniFi integration."""

from copy import deepcopy
import asyncio
import ipaddress
from json import JSONDecodeError
import logging
import re

from aiohttp import ClientError, ClientSession, ClientTimeout, CookieJar, Fingerprint

from ..core import SyncError
from ..client_observation import canonical_mac

_LOGGER = logging.getLogger(__name__)


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
        """Read only active clients using the official parent's fixed API path."""
        from aiounifi.models.client import ClientListRequest

        return await self._read_identity_records(ClientListRequest.create())

    async def read_devices(self):
        """Read switch port records without accepting a host, path or credentials."""
        from aiounifi.models.device import DeviceListRequest

        return await self._read_identity_records(DeviceListRequest.create())

    def __init__(self, hass, parent_entry_id, *, tls_pin_sha256=None, route_api_key=None):
        self.hass = hass
        self.parent_entry_id = parent_entry_id
        if tls_pin_sha256 is not None and (
            not isinstance(tls_pin_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", tls_pin_sha256)
        ):
            raise SyncError("Invalid UniFi certificate pin")
        self.tls_pin_sha256 = tls_pin_sha256
        if route_api_key is not None and (
            not isinstance(route_api_key, str)
            or not 16 <= len(route_api_key) <= 512
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in route_api_key)
        ):
            raise SyncError("Invalid dedicated UniFi route API key")
        self.route_api_key = route_api_key
        self._pinned_session = None
        self._pinned_controller = None
        self._pinned_parent = None
        self._pinned_lock = asyncio.Lock()

    def _api(self):
        entry = self.hass.config_entries.async_get_entry(self.parent_entry_id)
        hub = getattr(entry, "runtime_data", None) if entry else None
        if entry is None or entry.domain != "unifi" or hub is None or not getattr(hub, "api", None):
            raise SyncError("The selected UniFi integration is not loaded")
        return hub.api

    async def _trusted_api(self):
        """Log in independently using the parent's in-memory credentials and a TLS pin.

        The official parent remains the only credential store. Every route
        request, including login and reauthentication, verifies the exact
        gateway certificate. Its unverified session is never reused here.
        """
        if self.tls_pin_sha256 is None:
            return self._api()
        from aiounifi.controller import Controller
        from aiounifi.models.configuration import Configuration

        async with self._pinned_lock:
            parent = self._api()
            if self._pinned_controller is not None and parent is self._pinned_parent:
                return self._pinned_controller
            if self._pinned_session is not None:
                await self._pinned_session.close()
            self._pinned_session = ClientSession(cookie_jar=CookieJar(unsafe=True), trust_env=False)
            native = parent.connectivity.config
            controller = Controller(
                Configuration(
                    self._pinned_session,
                    host=native.host,
                    username=native.username,
                    password=native.password,
                    port=native.port,
                    site=native.site,
                    ssl_context=Fingerprint(bytes.fromhex(self.tls_pin_sha256)),
                    totp_secret=native.totp_secret,
                )
            )
            try:
                async with asyncio.timeout(15):
                    await controller.login()
            except BaseException:
                await self._pinned_session.close()
                self._pinned_session = None
                self._pinned_controller = None
                self._pinned_parent = None
                raise
            self._pinned_controller = controller
            self._pinned_parent = parent
            return controller

    async def async_close(self):
        async with self._pinned_lock:
            if self._pinned_session is not None:
                await self._pinned_session.close()
                self._pinned_session = None
            self._pinned_controller = None
            self._pinned_parent = None

    async def _request(self, request):
        """Keep API error bodies out of HA logs and diagnostics.

        Parent authentication stays owned by the official UniFi integration.
        Cancellation is deliberately not caught.
        """
        from aiounifi.errors import AiounifiException

        if self.route_api_key is not None and request.path.startswith("/trafficroutes"):
            return await self._route_key_request(request)

        try:
            return await (await self._trusted_api()).request(request)
        except (AiounifiException, ClientError, JSONDecodeError, TimeoutError, OSError) as exc:
            _LOGGER.warning("UniFi API request failed (%s)", type(exc).__name__)
            raise SyncError("The selected UniFi API request failed") from None

    async def _route_key_request(self, request):
        """Use a dedicated key only for fixed traffic-route paths, pinned end to end."""
        if self.tls_pin_sha256 is None or request.method not in ("get", "put"):
            raise SyncError("Verified TLS and a fixed route operation are required")
        if request.method == "get" and request.path != "/trafficroutes":
            raise SyncError("Unexpected UniFi route path")
        if request.method == "put" and not re.fullmatch(
            r"/trafficroutes/[0-9a-f-]{36}", request.path
        ):
            raise SyncError("Unexpected UniFi route path")
        native = self._api().connectivity.config
        host, site, port = native.host, native.site, native.port
        if (
            not isinstance(host, str)
            or not isinstance(site, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", site)
            or type(port) is not int
            or not 1 <= port <= 65535
        ):
            raise SyncError("Invalid trusted UniFi endpoint")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
                raise SyncError("Invalid trusted UniFi endpoint") from None
        if self._pinned_session is None:
            self._pinned_session = ClientSession(cookie_jar=CookieJar(unsafe=True), trust_env=False)
        url = f"https://{host}:{port}/proxy/network/v2/api/site/{site}{request.path}"
        try:
            async with self._pinned_session.request(
                request.method.upper(),
                url,
                headers={"X-API-KEY": self.route_api_key},
                json=deepcopy(request.data) if request.method == "put" else None,
                ssl=Fingerprint(bytes.fromhex(self.tls_pin_sha256)),
                timeout=ClientTimeout(total=15),
                allow_redirects=False,
            ) as response:
                if response.status not in (200, 201, 204):
                    _LOGGER.warning(
                        "Dedicated UniFi route API rejected request (HTTP %s)", response.status
                    )
                    raise SyncError("The dedicated UniFi route API request failed")
                if request.method == "put":
                    return None
                return await response.json(content_type=None)
        except (ClientError, JSONDecodeError, TimeoutError, OSError):
            raise SyncError("The dedicated UniFi route API request failed") from None

    async def read_routes(self):
        from aiounifi.models.traffic_route import TrafficRouteListRequest

        response = await self._request(TrafficRouteListRequest.create())
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
        from aiounifi.models.traffic_route import TrafficRouteSaveRequest

        if self.tls_pin_sha256 is None or self.route_api_key is None:
            raise SyncError(
                "A pinned certificate and dedicated API key are required for route writes"
            )
        await self._request(TrafficRouteSaveRequest.create(deepcopy(route)))

    async def read_gateways(self) -> dict[str, dict]:
        """Read WAN-capable devices using the parent's authenticated API.

        The device list request has a fixed path. No user-supplied host, URL or
        credentials are accepted here, and this does not modify the parent's
        cached objects or publish private interface addresses as attributes.
        """
        from aiounifi.models.device import DeviceListRequest

        response = await self._request(DeviceListRequest.create())
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
