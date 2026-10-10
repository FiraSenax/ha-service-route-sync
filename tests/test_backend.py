from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import bootstrap  # noqa: F401
from service_route_sync.backends.unifi import UniFiBackend
from service_route_sync.core import SyncError


class UniFiTests(unittest.IsolatedAsyncioTestCase):
    def backend(self, response):
        api = SimpleNamespace(request=AsyncMock(return_value=response))
        parent = SimpleNamespace(domain="unifi", runtime_data=SimpleNamespace(api=api))
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda _: parent))
        return UniFiBackend(hass, "example-entry"), api

    async def test_reads_existing_parent_api_without_new_login(self):
        backend, api = self.backend([{"_id": "route", "enabled": False}])
        self.assertEqual(await backend.read_routes(), {"route": {"_id": "route", "enabled": False}})
        request = api.request.call_args.args[0]
        self.assertEqual((request.method, request.path), ("get", "/trafficroutes"))

    async def test_save_preserves_disabled_state_and_scope(self):
        backend, api = self.backend([])
        original = {
            "_id": "route",
            "enabled": False,
            "network_id": "example-wan",
            "target_devices": [{"type": "ALL_CLIENTS"}],
        }
        expected = deepcopy(original)
        await backend.write_route(original)
        request = api.request.call_args.args[0]
        self.assertEqual((request.method, request.path), ("put", "/trafficroutes/route"))
        self.assertEqual(request.data, expected)
        request.data["enabled"] = True
        self.assertEqual(original, expected)

    async def test_rejects_unexpected_response(self):
        backend, _ = self.backend({"unexpected": "shape"})
        with self.assertRaises(SyncError):
            await backend.read_routes()

    async def test_unloaded_parent_is_not_replaced_by_another_login(self):
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda _: None))
        with self.assertRaises(SyncError):
            await UniFiBackend(hass, "missing").read_routes()
