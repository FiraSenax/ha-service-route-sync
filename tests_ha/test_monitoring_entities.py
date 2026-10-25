from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from custom_components.service_route_sync.monitoring.monitoring_coordinator import (
    MonitoringCoordinator,
)
from custom_components.service_route_sync.sensor import MonitoringStatusSensor
from custom_components.service_route_sync.binary_sensor import (
    MonitoringSourceSensor,
    MonitoringNotificationSensor,
)
from custom_components.service_route_sync.runtime import RuntimeData
from custom_components.service_route_sync.diagnostics import async_get_config_entry_diagnostics


class EntityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        state = {"engine": {"incidents": {"synthetic": {"active": True}}}}
        transaction = SimpleNamespace(store=SimpleNamespace(state=state))
        readers = {key: AsyncMock() for key in ("api", "probe", "dns", "windows")}
        self.coordinator = MonitoringCoordinator(self.hass, None, transaction, AsyncMock(), readers)
        self.coordinator.config_entry = SimpleNamespace(entry_id="entitytest")
        self.coordinator.data = {
            "observed_at": dt_util.utcnow().timestamp(),
            "pending": 2,
            "sources": {"api": True, "probe": False, "dns": True, "windows": False},
            "notification_ok": False,
        }
        self.runtime = SimpleNamespace(
            incidents=self.coordinator, active=False, profile_name="Office PC"
        )

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_mode_and_incident_count_are_visible_but_shadow_delivery_is_unavailable(self):
        self.assertEqual(MonitoringStatusSensor(self.runtime, "mode").native_value, "observation")
        self.assertEqual(MonitoringStatusSensor(self.runtime, "pending").native_value, 2)
        self.assertEqual(MonitoringStatusSensor(self.runtime, "active_incidents").native_value, 1)
        self.assertFalse(MonitoringNotificationSensor(self.runtime, "notification").available)
        self.runtime.active = True
        sensor = MonitoringNotificationSensor(self.runtime, "notification")
        self.assertTrue(sensor.available)
        self.assertTrue(sensor.is_on)

    async def test_old_and_future_observations_are_unavailable(self):
        sensor = MonitoringSourceSensor(self.runtime, "probe")
        self.assertTrue(sensor.available)
        self.assertFalse(sensor.is_on)
        for delta in (-76, 60):
            self.coordinator.data["observed_at"] = dt_util.utcnow().timestamp() + delta
            self.assertFalse(sensor.available)

    async def test_diagnostics_include_only_bounded_status(self):
        entry = SimpleNamespace(runtime_data=RuntimeData(monitoring=self.runtime))
        report = await async_get_config_entry_diagnostics(self.hass, entry)
        self.assertEqual(
            set(report["monitoring"]),
            {
                "active",
                "last_update_success",
                "observed_at",
                "sources",
                "pending",
                "notification_ok",
            },
        )
        self.assertNotIn("synthetic", str(report))
