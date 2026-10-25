"""Notification activation uses explicit configuration."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_ha import CONNECTION

from tests_ha.test_wifi_flow import Harness


class MonitoringModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = Harness()
        self.flow.entry = SimpleNamespace(
            entry_id="entry",
            data={**CONNECTION},
            options={"monitoring_enabled": True, "monitoring_active": False},
            runtime_data=SimpleNamespace(
                monitoring=SimpleNamespace(incidents=SimpleNamespace(last_update_success=True))
            ),
        )
        self.flow.hass = SimpleNamespace(
            config=SimpleNamespace(path=lambda *_: "/tmp/monitoring-mode-test"),
            async_add_executor_job=AsyncMock(return_value={}),
        )
        await self.flow.async_step_init()

    async def test_activation_requires_explicit_notification_target(self):
        result = await self.flow.async_step_monitoring_mode({"monitoring_active": True})
        self.assertEqual(result["errors"], {"base": "notification_target_required"})

    async def test_new_installation_activates_without_personal_checkpoint(self):
        self.flow.defaults["monitoring_notify_service"] = "test_target"
        result = await self.flow.async_step_monitoring_mode({"monitoring_active": True})
        self.assertEqual(result["type"], "create_entry")
        self.assertTrue(result["data"]["monitoring_active"])
        self.flow.defaults = result["data"]
        result = await self.flow.async_step_monitoring_mode({"monitoring_active": False})
        self.assertFalse(result["data"]["monitoring_active"])
