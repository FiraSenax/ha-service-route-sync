from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from custom_components.service_route_sync.wifi_coordinator import WifiCoordinator
from custom_components.service_route_sync.core import SyncError
from custom_components.service_route_sync.wifi_history import WifiHistory


class WirelessCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_storage_failure_does_not_publish_a_successful_new_scan(self):
        history = WifiHistory(Path(self.temp.name)/'private'/'wifi.sqlite')
        self.coordinator.history = history
        with patch.object(self.coordinator.backend,'read_clients',AsyncMock(return_value=[self.row])):
            await self.coordinator.async_refresh()
            previous = self.coordinator.data
            with patch.object(history,'record',side_effect=OSError('synthetic')):
                await self.coordinator.async_refresh()
        self.assertFalse(self.coordinator.last_update_success)
        self.assertIs(self.coordinator.data,previous)
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.coordinator = WifiCoordinator(self.hass, None, 'parent')
        self.row = {'mac': '02:00:00:00:00:01', 'is_wired': False,
                    'last_seen': dt_util.utcnow().timestamp(), 'signal': -60}

    async def asyncTearDown(self):
        await self.coordinator.async_shutdown()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_failed_fetch_keeps_last_good_snapshot_and_reports_failure(self):
        with patch.object(self.coordinator.backend, 'read_clients', AsyncMock(return_value=[self.row])):
            await self.coordinator.async_refresh()
        previous = self.coordinator.data
        with patch.object(self.coordinator.backend, 'read_clients', AsyncMock(side_effect=SyncError('synthetic'))):
            await self.coordinator.async_refresh()
        self.assertFalse(self.coordinator.last_update_success)
        self.assertIs(self.coordinator.data, previous)

    async def test_empty_complete_list_is_distinct_from_invalid_list(self):
        with patch.object(self.coordinator.backend, 'read_clients', AsyncMock(return_value=[])):
            await self.coordinator.async_refresh()
        self.assertTrue(self.coordinator.last_update_success)
        self.assertEqual(self.coordinator.data.clients, ())
        previous = self.coordinator.data
        with patch.object(self.coordinator.backend, 'read_clients', AsyncMock(return_value=[self.row, self.row])):
            await self.coordinator.async_refresh()
        self.assertFalse(self.coordinator.last_update_success)
        self.assertIs(self.coordinator.data, previous)
