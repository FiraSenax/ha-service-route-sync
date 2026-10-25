from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_ha.test_network_flow import FlowHarness
from custom_components.service_route_sync.core import SyncError

MAC = "02:00:00:00:00:01"
OTHER = "02:00:00:00:00:02"


class MonitoringFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = FlowHarness()
        self.flow.settings.update(monitoring_enabled=True)
        self.backend = SimpleNamespace(
            async_close=AsyncMock(),
            read_clients=AsyncMock(return_value=[{"mac": MAC, "name": "Synthetic Monitoring"}]),
        )
        self.patch = patch(
            "custom_components.service_route_sync.monitoring_flow.UniFiBackend",
            return_value=self.backend,
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_first_configuration_requires_source_setup_and_starts_observation(self):
        self.flow.async_step_monitoring_sources = AsyncMock(
            return_value={"type": "form", "step_id": "monitoring_sources"}
        )
        result = await self.flow.async_step_monitoring({"monitoring_clients": [MAC]})
        self.assertEqual(result["step_id"], "monitoring_sources")
        self.assertFalse(self.flow.settings["monitoring_active"])
        self.assertEqual(self.flow.settings["monitoring_profile"]["id"], "client")
        self.assertEqual(self.flow.settings.get("client_profiles", []), [])

    async def test_unknown_duplicate_and_existing_gaming_clients_are_rejected(self):
        for selection in ([OTHER], [MAC, MAC], []):
            result = await self.flow.async_step_monitoring({"monitoring_clients": selection})
            self.assertTrue(result["errors"])
        self.flow.settings["client_profiles"] = [{"id": "gaming", "macs": [MAC]}]
        result = await self.flow.async_step_monitoring({"monitoring_clients": [MAC]})
        self.assertEqual(result["errors"], {"base": "invalid_clients"})

    async def test_known_offline_mac_can_be_entered_explicitly(self):
        self.flow.async_step_monitoring_sources = AsyncMock(
            return_value={"type": "form", "step_id": "monitoring_sources"}
        )
        result = await self.flow.async_step_monitoring(
            {"monitoring_clients": [], "monitoring_manual_mac": OTHER.upper()}
        )
        self.assertEqual(result["step_id"], "monitoring_sources")
        self.assertEqual(self.flow.settings["monitoring_profile"]["macs"], [OTHER])

    async def test_invalid_or_reused_manual_mac_is_rejected(self):
        self.flow.settings["client_profiles"] = [{"id": "gaming", "macs": [OTHER]}]
        for candidate in (OTHER, "ff:ff:ff:ff:ff:ff", "not-a-mac"):
            result = await self.flow.async_step_monitoring(
                {"monitoring_clients": [], "monitoring_manual_mac": candidate}
            )
            self.assertEqual(result["errors"], {"base": "invalid_clients"})

    async def test_existing_offline_aliases_survive_unrelated_options_update(self):
        self.backend.read_clients.side_effect = SyncError("synthetic")
        self.flow.settings.update(
            monitoring_profile={"id": "office_pc", "name": "Office computer", "macs": [MAC, OTHER]},
            monitoring_active=True,
            monitoring_notify_service="test_target",
            monitoring_dsl_key_id="a" * 32,
            monitoring_pc_key_id="b" * 32,
            monitoring_windows_key_id="c" * 32,
            monitoring_dns_servers=["192.168.1.10", "192.168.1.11"],
        )
        result = await self.flow.async_step_monitoring({"monitoring_clients": [MAC, OTHER]})
        self.assertEqual(result["type"], "create_entry")
        self.assertTrue(result["data"]["monitoring_active"])
        self.assertEqual(result["data"]["monitoring_profile"]["macs"], [MAC, OTHER])

    async def test_dns_options_require_two_fixed_lan_resolvers(self):
        invalid = await self.flow.async_step_monitoring_dns(
            {"primary": "192.168.1.10", "secondary": "8.8.8.8"}
        )
        self.assertEqual(invalid["errors"], {"base": "invalid_dns_servers"})
        saved = await self.flow.async_step_monitoring_dns(
            {"primary": "192.168.1.10", "secondary": "192.168.1.11"}
        )
        self.assertEqual(saved["type"], "create_entry")
        self.assertEqual(saved["data"]["monitoring_dns_servers"], ["192.168.1.10", "192.168.1.11"])

    async def test_invalid_existing_profile_has_sanitized_failure(self):
        self.flow.settings["monitoring_profile"] = {"id": "bad id!", "macs": [MAC]}
        result = await self.flow.async_step_monitoring()
        self.assertEqual(result["reason"], "invalid_monitoring_configuration")

    async def test_sources_offer_only_existing_pinned_management_host(self):
        self.flow.settings.update(
            client_probe_enabled=True,
            gateway_observer_enabled=True,
            client_probe_host="172.20.0.1",
            gateway_observer_host="172.20.0.1",
        )
        with (
            patch(
                "custom_components.service_route_sync.monitoring_flow.prepare_observer_key",
                new_callable=AsyncMock,
                return_value="synthetic-public-key",
            ),
            patch(
                "custom_components.service_route_sync.monitoring_flow.credential_directory",
                return_value=Path("/tmp/synthetic"),
            ),
        ):
            result = await self.flow.async_step_monitoring_sources()
        self.assertEqual(result["type"], "form")
        selected = next(
            value.config["options"]
            for key, value in result["data_schema"].schema.items()
            if str(key) == "monitoring_gateway_host"
        )
        self.assertEqual(selected, [{"value": "172.20.0.1", "label": "172.20.0.1"}])
        self.backend.read_gateways = AsyncMock(side_effect=AssertionError("WAN lookup forbidden"))
        self.backend.read_gateways.assert_not_called()

    async def test_sources_reject_mismatched_management_peer(self):
        self.flow.settings.update(
            client_probe_enabled=True,
            gateway_observer_enabled=True,
            client_probe_host="172.20.0.1",
            gateway_observer_host="172.20.0.2",
        )
        with patch(
            "custom_components.service_route_sync.monitoring_flow.prepare_observer_key",
            new_callable=AsyncMock,
            return_value="synthetic-public-key",
        ):
            result = await self.flow.async_step_monitoring_sources()
        self.assertEqual(result["reason"], "observer_address_unavailable")
