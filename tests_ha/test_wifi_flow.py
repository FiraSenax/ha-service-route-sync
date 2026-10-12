from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.service_route_sync.config_flow import OptionsFlow
from custom_components.service_route_sync.core import SyncError


class Harness(OptionsFlow):
    @property
    def config_entry(self):
        return self.entry

    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def finish(self):
        return {"type": "create_entry", "data": self.settings}


class WifiFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = Harness()
        self.flow.entry = SimpleNamespace(
            data={"unifi_entry_id": "parent"},
            options={
                "gateway_observer_host": "trusted-existing-host",
                "gateway_observer_key_id": "existing-key",
                "client_profiles": [{"id": "synthetic", "macs": []}],
                "automatic_apply": False,
            },
        )
        self.flow.hass = SimpleNamespace()
        self.backend = SimpleNamespace(read_clients=AsyncMock(return_value=[]))
        self.patch = patch(
            "custom_components.service_route_sync.config_flow.UniFiBackend",
            return_value=self.backend,
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.menu = await self.flow.async_step_init()

    async def test_enable_preserves_existing_trust_and_profiles(self):
        self.assertEqual(
            self.menu["menu_options"],
            [
                "user",
                "route_services",
                "gateway_management",
                "wifi_history",
                "frigate_actor",
                "lady_mode",
            ],
        )
        result = await self.flow.async_step_wifi_history({"wifi_history_enabled": True})
        self.assertEqual(result["type"], "create_entry")
        for key, value in self.flow.entry.options.items():
            self.assertEqual(result["data"][key], value)
        self.assertTrue(result["data"]["wifi_history_enabled"])
        self.backend.read_clients.assert_awaited_once()

    async def test_failed_source_does_not_save_and_disabling_needs_no_source(self):
        self.backend.read_clients.side_effect = SyncError("synthetic")
        result = await self.flow.async_step_wifi_history({"wifi_history_enabled": True})
        self.assertEqual(result["type"], "form")
        self.assertTrue(result["errors"])
        self.backend.read_clients.reset_mock()
        result = await self.flow.async_step_wifi_history({"wifi_history_enabled": False})
        self.assertEqual(result["type"], "create_entry")
        self.backend.read_clients.assert_not_awaited()
