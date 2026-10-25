"""Read-only network configuration uses discovered choices and validated inputs."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_ha import CONNECTION
from custom_components.service_route_sync.config_flow import OptionsFlow, Steps
from custom_components.service_route_sync.const import DEFAULTS


class FlowHarness(Steps):
    def __init__(self):
        self.defaults = {**DEFAULTS, **CONNECTION}
        self.settings = {
            **DEFAULTS,
            **CONNECTION,
            "services": [],
            "network_enabled": True,
        }
        self.hass = SimpleNamespace(
            services=SimpleNamespace(async_services=lambda: {"notify": {"test_target": {}}}),
            config_entries=SimpleNamespace(
                async_entries=lambda domain: [SimpleNamespace(entry_id="parent", title="Router")]
            ),
        )

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    def finish(self):
        return {"type": "create_entry", "data": self.settings}


class NetworkFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_wifi_only_is_valid_without_routes_or_wan(self):
        result = await self.flow.async_step_user(
            {**CONNECTION, "services": [], "wifi_history_enabled": True}
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertTrue(result["data"]["wifi_history_enabled"])
        self.backend.read_gateways.assert_not_awaited()

    async def test_wifi_option_preserves_existing_choice_and_can_be_disabled(self):
        self.flow.defaults["wifi_history_enabled"] = True
        result = await self.flow.async_step_user(
            {**CONNECTION, "services": [], "network_enabled": True}
        )
        self.assertEqual(result["step_id"], "network")
        self.assertTrue(self.flow.settings["wifi_history_enabled"])
        await self.flow.async_step_user(
            {
                **CONNECTION,
                "services": [],
                "network_enabled": True,
                "wifi_history_enabled": False,
            }
        )
        self.assertFalse(self.flow.settings["wifi_history_enabled"])

    async def asyncSetUp(self):
        self.flow = FlowHarness()
        self.backend = SimpleNamespace(
            async_close=AsyncMock(),
            read_devices=AsyncMock(return_value=[]),
            read_gateways=AsyncMock(return_value={"gateway": {"name": "Router"}}),
            read_routes=AsyncMock(return_value={}),
        )
        self.patch = patch(
            "custom_components.service_route_sync.config_flow.UniFiBackend",
            return_value=self.backend,
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_network_only_skips_route_api(self):
        result = await self.flow.async_step_user(
            {**CONNECTION, "services": [], "network_enabled": True}
        )
        self.assertEqual(result["step_id"], "network")
        self.backend.read_gateways.assert_awaited_once()

    async def test_empty_or_unknown_service_selection_does_not_crash(self):
        for services in ([], ["nonexistent"]):
            result = await self.flow.async_step_user({**CONNECTION, "services": services})
            self.assertEqual(result["type"], "form")
            self.assertTrue(result["errors"])

    async def test_disabled_network_makes_no_device_request(self):
        self.flow.settings["network_enabled"] = False
        self.assertEqual((await self.flow.async_step_network())["type"], "create_entry")
        self.backend.read_gateways.assert_not_awaited()

    async def test_discovered_gateway_and_labels_are_validated(self):
        data = {
            "network_gateway_id": "gateway",
            "network_primary_label": " DSL ",
            "network_secondary_label": "Starlink",
            "network_interval_seconds": 10,
        }
        result = await self.flow.async_step_network(data)
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["network_primary_label"], "DSL")
        for override in (
            {"network_gateway_id": "missing"},
            {"network_primary_label": " "},
            {"network_interval_seconds": 0},
            {"unexpected": "value"},
        ):
            result = await self.flow.async_step_network({**data, **override})
            self.assertEqual(result["type"], "form")
            self.assertEqual(result["errors"], {"base": "invalid_gateway"})

    async def test_no_gateway_or_api_failure_aborts_without_writes(self):
        self.backend.read_gateways.return_value = {}
        self.assertEqual((await self.flow.async_step_network())["reason"], "no_gateways")
        self.backend.read_gateways.side_effect = TimeoutError()
        self.assertEqual((await self.flow.async_step_network())["reason"], "router_unavailable")

    async def test_scoped_route_options_preserve_network_and_other_features(self):
        self.flow.defaults.update(
            services=["blizzard"],
            wifi_history_enabled=True,
            gateway_observer_enabled=True,
            network_enabled=True,
        )
        self.backend.read_routes.return_value = {
            "r1": {
                "_id": "r1",
                "description": "Blizzard",
                "matching_target": "IP",
                "domains": [],
                "regions": [],
            },
            "r2": {
                "_id": "r2",
                "description": "Riot",
                "matching_target": "IP",
                "domains": [],
                "regions": [],
            },
        }
        result = await OptionsFlow.async_step_route_services(
            self.flow, {"services": ["blizzard", "riot"]}
        )
        self.assertEqual(result["step_id"], "routes")
        self.assertEqual(result["type"], "form")
        result = await self.flow.async_step_routes({"blizzard": "r1", "riot": "r2"})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["services"], ["blizzard", "riot"])
        self.assertEqual(result["data"]["bindings"], {"blizzard": "r1", "riot": "r2"})
        self.assertTrue(result["data"]["wifi_history_enabled"])
        self.assertTrue(result["data"]["gateway_observer_enabled"])
        self.backend.read_gateways.assert_not_awaited()

    async def test_scoped_route_options_reject_unknown_and_can_clear(self):
        invalid = await OptionsFlow.async_step_route_services(self.flow, {"services": ["unknown"]})
        self.assertEqual(invalid["errors"]["base"], "invalid_configuration")
        cleared = await OptionsFlow.async_step_route_services(self.flow, {"services": []})
        self.assertEqual(cleared["type"], "create_entry")
        self.assertEqual(cleared["data"]["services"], [])
        self.backend.read_gateways.assert_not_awaited()
