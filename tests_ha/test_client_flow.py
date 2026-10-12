from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_ha.test_network_flow import FlowHarness
from custom_components.service_route_sync.core import SyncError

MAC = "02:00:00:00:00:01"


class ClientFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = FlowHarness()
        self.flow.settings.update(client_monitoring_enabled=True, network_enabled=False)
        self.backend = SimpleNamespace(
            read_clients=AsyncMock(return_value=[{"mac": MAC, "name": "PC"}])
        )
        self.patch = patch(
            "custom_components.service_route_sync.config_flow.UniFiBackend",
            return_value=self.backend,
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_client_only_setup_skips_route_and_gateway_requests(self):
        result = await self.flow.async_step_user(
            {"unifi_entry_id": "parent", "services": [], "client_monitoring_enabled": True}
        )
        self.assertEqual(result["step_id"], "clients")
        result = await self.flow.async_step_clients({"clients": ["client_020000000001"]})
        self.assertEqual(result["data"]["client_profiles"][0]["macs"], [MAC])

    async def test_existing_offline_group_preserved_with_all_aliases(self):
        profiles = [{"id": "stable", "name": "PC", "macs": [MAC, "02:00:00:00:00:02"]}]
        self.flow.settings["client_profiles"] = profiles
        self.backend.read_clients.return_value = []
        result = await self.flow.async_step_clients({"clients": ["stable"]})
        self.assertEqual(result["data"]["client_profiles"], profiles)

    async def test_unrecognized_empty_and_duplicate_selections_rejected(self):
        for values in [[], ["unknown"], ["client_020000000001"] * 2]:
            result = await self.flow.async_step_clients({"clients": values})
            self.assertEqual(result["errors"], {"base": "invalid_clients"})

    async def test_api_failure_and_disabled_feature(self):
        self.backend.read_clients.side_effect = SyncError("private")
        self.assertEqual((await self.flow.async_step_clients())["reason"], "router_unavailable")
        self.flow.settings["client_monitoring_enabled"] = False
        self.flow.settings["client_profiles"] = [{"id": "stable", "macs": [MAC]}]
        result = await self.flow.async_step_clients()
        self.assertEqual(result["data"]["client_profiles"], [])

    async def test_probe_access_requires_valid_full_profile_handshake(self):
        from homeassistant.util import dt as dt_util

        self.flow.settings.update(
            client_probe_key_id="1" * 32, client_profiles=[{"id": "pc", "macs": [MAC]}]
        )
        self.backend.read_gateways = AsyncMock(
            return_value={"gateway": {"ip": "172.20.0.1", "name": "Router"}}
        )
        self.flow.hass.async_add_executor_job = AsyncMock()
        raw = {
            "schema": 1,
            "observed_at_ms": int(dt_util.utcnow().timestamp() * 1000),
            "clients": {
                "pc": {
                    "identity_verified": False,
                    "probe_at_ms": None,
                    "probe": None,
                    "routing_at_ms": None,
                    "connections": None,
                }
            },
        }
        with (
            patch(
                "custom_components.service_route_sync.config_flow.prepare_observer_key",
                AsyncMock(return_value="public"),
            ),
            patch(
                "custom_components.service_route_sync.config_flow.validate_host_key",
                return_value="pinned",
            ),
            patch(
                "custom_components.service_route_sync.config_flow.credential_directory",
                return_value=Path("/tmp/example-probe-identity"),
            ),
            patch(
                "custom_components.service_route_sync.config_flow.read_client_probes",
                AsyncMock(return_value=raw),
            ),
        ):
            result = await self.flow.async_step_client_probe(
                {"client_probe_host": "172.20.0.1", "gateway_host_public_key": "public"}
            )
            self.assertEqual(result["type"], "create_entry")
            raw["clients"] = {}
            result = await self.flow.async_step_client_probe(
                {"client_probe_host": "172.20.0.1", "gateway_host_public_key": "public"}
            )
            self.assertEqual(result["errors"], {"base": "observer_unavailable"})

    async def test_work_only_configuration_skips_unifi_client_reads(self):
        result = await self.flow.async_step_user(
            {"unifi_entry_id": "parent", "services": [], "work_probe_enabled": True}
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertTrue(result["data"]["work_probe_enabled"])
        self.backend.read_clients.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
