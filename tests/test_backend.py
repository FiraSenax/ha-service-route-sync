from copy import deepcopy
from json import JSONDecodeError
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from aiohttp import Fingerprint

import bootstrap  # noqa: F401
from service_route_sync.backends.unifi import UniFiBackend
from service_route_sync.core import SyncError


class UniFiTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_controller_uses_parent_credentials_but_separate_verified_session(self):
        native = SimpleNamespace(host="192.0.2.1", username="test", password="secret",
                                 port=443, site="default", totp_secret=None,
                                 session=object())
        parent_api = SimpleNamespace(connectivity=SimpleNamespace(config=native))
        parent = SimpleNamespace(domain="unifi", runtime_data=SimpleNamespace(api=parent_api))
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda _: parent))
        backend = UniFiBackend(hass, "parent", tls_pin_sha256="a" * 64)
        with patch("aiounifi.controller.Controller") as controller_class:
            controller = controller_class.return_value
            controller.login = AsyncMock()
            try:
                self.assertIs(await backend._trusted_api(), controller)
                self.assertIs(await backend._trusted_api(), controller)
                controller.login.assert_awaited_once()
                config = controller_class.call_args.args[0]
                self.assertIsInstance(config.ssl_context, Fingerprint)
                self.assertEqual(config.ssl_context.fingerprint, bytes.fromhex("a" * 64))
                self.assertIsNot(config.session, native.session)
                self.assertEqual((config.username, config.password), ("test", "secret"))
            finally:
                await backend.async_close()

    async def test_client_and_device_reads_use_fixed_parent_paths(self):
        for method, expected in [("read_clients", "/stat/sta"), ("read_devices", "/stat/device")]:
            source = {"data": [{"mac": "02:00:00:00:00:01", "port_table": [{"speed": 2500}]}]}
            backend, api = self.backend(source)
            rows = await getattr(backend, method)()
            request = api.request.call_args.args[0]
            self.assertEqual((request.method, request.path), ("get", expected))
            rows[0]["port_table"][0]["speed"] = 0
            self.assertEqual(source["data"][0]["port_table"][0]["speed"], 2500)

    async def test_client_identity_response_must_be_complete_and_unambiguous(self):
        for source in [
            {},
            {"data": None},
            {"data": [None]},
            {"data": [{"mac": "invalid"}]},
            {"data": [{"mac": "02:00:00:00:00:01"}, {"mac": "02:00:00:00:00:01"}]},
        ]:
            backend, _ = self.backend(source)
            with self.assertRaises(SyncError):
                await backend.read_clients()

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
        with self.assertRaisesRegex(SyncError, "pinned certificate"):
            await backend.write_route(original)
        backend.tls_pin_sha256 = "a" * 64
        backend.route_api_key = "x" * 32
        with patch.object(backend, "_route_key_request", AsyncMock()) as route_request:
            await backend.write_route(original)
        request = route_request.call_args.args[0]
        self.assertEqual((request.method, request.path), ("put", "/trafficroutes/route"))
        self.assertEqual(request.data, expected)
        request.data["enabled"] = True
        self.assertEqual(original, expected)

    async def test_rejects_unexpected_response(self):
        backend, _ = self.backend({"unexpected": "shape"})
        with self.assertRaises(SyncError):
            await backend.read_routes()

    async def test_rejects_duplicate_or_nonstring_route_identity(self):
        for rows in ([{"_id": 42}], [{"_id": "same"}, {"_id": "same"}], [None]):
            backend, _ = self.backend(rows)
            with self.assertRaises(SyncError):
                await backend.read_routes()

    async def test_unloaded_parent_is_not_replaced_by_another_login(self):
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda _: None))
        with self.assertRaises(SyncError):
            await UniFiBackend(hass, "missing").read_routes()

    async def test_gateway_reads_fixed_path_and_keeps_parent_data_private(self):
        source = {
            "data": [
                {"_id": "switch", "name": "Switch"},
                {"_id": "gateway", "wan1": {"up": True, "ip": "private-example"}},
            ]
        }
        backend, api = self.backend(source)
        rows = await backend.read_gateways()
        self.assertEqual(set(rows), {"gateway"})
        request = api.request.call_args.args[0]
        self.assertEqual((request.method, request.path), ("get", "/stat/device"))
        rows["gateway"]["wan1"]["up"] = False
        self.assertTrue(source["data"][1]["wan1"]["up"])

    async def test_gateway_response_rejects_ambiguous_identity(self):
        for data in (
            {},
            {"data": {}},
            {"data": [None]},
            {"data": [{"_id": "same", "wan1": {}}, {"_id": "same", "wan2": {}}]},
            {"data": [{"_id": 42, "wan1": {}}]},
        ):
            backend, _ = self.backend(data)
            with self.assertRaises(SyncError):
                await backend.read_gateways()

    async def test_api_failure_does_not_include_private_response_body(self):
        from aiounifi.errors import Unauthorized

        backend, api = self.backend({})
        api.request.side_effect = Unauthorized("private-example")
        with self.assertRaises(SyncError) as captured:
            await backend.read_gateways()
        self.assertNotIn("private-example", str(captured.exception))
        self.assertTrue(captured.exception.__suppress_context__)

    async def test_truncated_unifi_json_is_a_sanitized_transient_failure(self):
        backend, api = self.backend({})
        api.request.side_effect = JSONDecodeError("private-response", "private-response", 0)
        with self.assertRaises(SyncError) as captured:
            await backend.read_clients()
        self.assertNotIn("private-response", str(captured.exception))
        self.assertTrue(captured.exception.__suppress_context__)
        api.request.side_effect = None
        api.request.return_value = {"data": [{"mac": "02:00:00:00:00:01"}]}
        self.assertEqual(len(await backend.read_clients()), 1)

    async def test_cancelled_read_propagates_without_retry_or_login(self):
        import asyncio

        backend, api = self.backend({})
        api.request.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await backend.read_gateways()
        self.assertEqual(api.request.await_count, 1)
