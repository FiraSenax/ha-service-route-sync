from datetime import timedelta
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from custom_components.service_route_sync.wifi_coordinator import WifiCoordinator, WirelessSnapshot
from custom_components.service_route_sync.wifi_entity import WifiStatusSensor
from custom_components.service_route_sync.runtime import RuntimeData
from custom_components.service_route_sync.diagnostics import async_get_config_entry_diagnostics


class WifiEntityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.coordinator = WifiCoordinator(self.hass, None, 'parent')
        self.coordinator.config_entry = SimpleNamespace(entry_id='wifi-test')
        self.now = dt_util.utcnow()
        self.coordinator.data = WirelessSnapshot(self.now, ())

    async def asyncTearDown(self):
        await self.coordinator.async_shutdown()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_empty_success_is_zero_without_client_attributes(self):
        sensor = WifiStatusSensor(self.coordinator, 'client_count')
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, 0)
        self.assertIsNone(sensor.extra_state_attributes)
        self.assertEqual(WifiStatusSensor(self.coordinator, 'last_observation').native_value, self.now)

    async def test_diagnostics_contain_only_aggregate_status(self):
        entry = SimpleNamespace(runtime_data=RuntimeData(wifi=self.coordinator))
        report = await async_get_config_entry_diagnostics(self.hass, entry)
        self.assertTrue(report['wifi_history_enabled'])
        self.assertEqual(set(report['wifi']), {'last_update_success', 'observed_at', 'client_count', 'history_enabled'})
        self.assertEqual(report['wifi']['client_count'], 0)

    async def test_failed_stale_future_and_missing_snapshots_are_unavailable(self):
        sensor = WifiStatusSensor(self.coordinator, 'client_count')
        self.coordinator.last_update_success = False
        self.assertFalse(sensor.available)
        self.coordinator.last_update_success = True
        for delta in (-76, 60):
            self.coordinator.data = WirelessSnapshot(self.now + timedelta(seconds=delta), ())
            self.assertFalse(sensor.available)
        self.coordinator.data = None
        self.assertFalse(sensor.available)
        self.assertIsNone(sensor.native_value)
