"""A durable import and stopped legacy monitor gate Lady activation."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests_ha.test_wifi_flow import Harness


class LadyModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = Harness()
        self.flow.entry = SimpleNamespace(
            entry_id="entry",
            data={"unifi_entry_id": "parent"},
            options={"lady_enabled": True, "lady_active": False},
            runtime_data=SimpleNamespace(
                lady=SimpleNamespace(incidents=SimpleNamespace(last_update_success=True))
            ),
        )
        self.flow.hass = SimpleNamespace(
            config=SimpleNamespace(path=lambda *_: "/tmp/lady-mode-test"),
            async_add_executor_job=AsyncMock(return_value={}),
        )
        await self.flow.async_step_init()

    async def test_activation_requires_legacy_freeze(self):
        result = await self.flow.async_step_lady_mode(
            {
                "lady_active": True,
                "legacy_monitor_stopped": False,
            }
        )
        self.assertEqual(result["errors"], {"base": "legacy_monitor_running"})

    async def test_activation_requires_durable_checkpoint(self):
        store = SimpleNamespace(async_load=AsyncMock(return_value=None))
        with patch(
            "custom_components.service_route_sync.config_flow.LadyCheckpointStore",
            return_value=store,
        ):
            result = await self.flow.async_step_lady_mode(
                {
                    "lady_active": True,
                    "legacy_monitor_stopped": True,
                }
            )
        self.assertEqual(result["errors"], {"base": "lady_checkpoint_missing"})

    async def test_activation_after_verified_import_and_deactivation(self):
        checkpoint = {
            "cursor": {"day": "2026-10-12", "offset": 1},
            "history_epoch": "a" * 64,
            "history_revision": 1,
        }
        store = SimpleNamespace(
            async_load=AsyncMock(return_value=checkpoint),
            store=SimpleNamespace(key="service_route_sync.lady.entry"),
        )
        with (
            patch(
                "custom_components.service_route_sync.config_flow.LadyCheckpointStore",
                return_value=store,
            ),
            patch("custom_components.service_route_sync.config_flow.LadyHistory") as history,
        ):
            result = await self.flow.async_step_lady_mode(
                {
                    "lady_active": True,
                    "legacy_monitor_stopped": True,
                }
            )
            history.assert_called_once()
        self.assertEqual(result["type"], "create_entry")
        self.assertTrue(result["data"]["lady_active"])
        self.flow.defaults = {**result["data"]}
        result = await self.flow.async_step_lady_mode({"lady_active": False})
        self.assertEqual(result["type"], "create_entry")
        self.assertFalse(result["data"]["lady_active"])


if __name__ == "__main__":
    unittest.main()
