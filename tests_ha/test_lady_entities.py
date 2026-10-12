from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from custom_components.service_route_sync.lady_coordinator import LadyCoordinator
from custom_components.service_route_sync.sensor import LadyStatusSensor
from custom_components.service_route_sync.binary_sensor import LadySourceSensor, LadyNotificationSensor
from custom_components.service_route_sync.runtime import RuntimeData
from custom_components.service_route_sync.diagnostics import async_get_config_entry_diagnostics


class EntityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        state = {'engine': {'incidents': {'synthetic': {'active': True}}}}
        transaction = SimpleNamespace(store=SimpleNamespace(state=state))
        readers = {key: AsyncMock() for key in ('api', 'probe', 'dns', 'windows')}
        self.coordinator = LadyCoordinator(self.hass, None, transaction, AsyncMock(), readers)
        self.coordinator.config_entry = SimpleNamespace(entry_id='entitytest')
        self.coordinator.data = {'observed_at': dt_util.utcnow().timestamp(), 'pending': 2,
                                 'sources': {'api': True, 'probe': False, 'dns': True, 'windows': False},
                                 'notification_ok': False}
        self.runtime = SimpleNamespace(incidents=self.coordinator, active=False)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_mode_and_incident_count_are_visible_but_shadow_delivery_is_unavailable(self):
        self.assertEqual(LadyStatusSensor(self.runtime, 'mode').native_value, 'observation')
        self.assertEqual(LadyStatusSensor(self.runtime, 'pending').native_value, 2)
        self.assertEqual(LadyStatusSensor(self.runtime, 'active_incidents').native_value, 1)
        self.assertFalse(LadyNotificationSensor(self.runtime, 'notification').available)
        self.runtime.active = True
        sensor = LadyNotificationSensor(self.runtime, 'notification')
        self.assertTrue(sensor.available)
        self.assertTrue(sensor.is_on)

    async def test_old_and_future_observations_are_unavailable(self):
        sensor = LadySourceSensor(self.runtime, 'probe')
        self.assertTrue(sensor.available)
        self.assertFalse(sensor.is_on)
        for delta in (-76, 60):
            self.coordinator.data['observed_at'] = dt_util.utcnow().timestamp() + delta
            self.assertFalse(sensor.available)

    async def test_diagnostics_include_only_bounded_status(self):
        entry = SimpleNamespace(runtime_data=RuntimeData(lady=self.runtime))
        report = await async_get_config_entry_diagnostics(self.hass, entry)
        self.assertEqual(set(report['lady']), {'active', 'last_update_success', 'observed_at', 'sources', 'pending', 'notification_ok'})
        self.assertNotIn('synthetic', str(report))
