from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import asyncio
import probatio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized
from custom_components.service_route_sync.wifi_api import register_wifi_api
from custom_components.service_route_sync.wifi_history import WifiHistoryError


class WifiApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.hass.config_entries = SimpleNamespace(async_get_entry=Mock())
        register_wifi_api(self.hass)
        self.handler, self.schema = self.hass.data['websocket_api']['service_route_sync/wifi_history']
        self.connection = SimpleNamespace(user=SimpleNamespace(is_admin=True), send_result=Mock(), send_error=Mock())
        self.history = SimpleNamespace(read_page=Mock(return_value={'rows': [], 'more': False}))
        self.entry = SimpleNamespace(entry_id='entry', domain='service_route_sync',
                                     runtime_data=SimpleNamespace(wifi=SimpleNamespace(history=self.history)))
        self.message = {'id': 1, 'type': 'service_route_sync/wifi_history', 'entry_id': 'entry', 'source': 'legacy'}

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_nonadmin_cannot_schedule_or_read_history(self):
        for user in (None, SimpleNamespace(is_admin=False)):
            self.connection.user = user
            with self.assertRaises(Unauthorized):
                self.handler(self.hass, self.connection, self.message)
        self.history.read_page.assert_not_called()

    async def test_schema_rejects_invalid_types_limits_and_unknown_fields(self):
        for extra in ({'after': True}, {'after': -1}, {'ceiling': 2**63},
                      {'limit': 101}, {'limit': 0}, {'source': 'scans'}, {'path': '/etc/passwd'}):
            with self.assertRaises(probatio.Invalid):
                self.schema({**self.message, **extra})

    async def test_missing_or_wrong_domain_entry_does_not_read(self):
        for entry in (None, SimpleNamespace(domain='other', runtime_data=self.entry.runtime_data)):
            with patch.object(self.hass.config_entries, 'async_get_entry', return_value=entry):
                self.handler(self.hass, self.connection, self.schema(self.message))
                await self.hass.async_block_till_done(wait_background_tasks=True)
        self.history.read_page.assert_not_called()
        self.assertEqual(self.connection.send_error.call_count, 2)

    async def test_cancelled_request_keeps_lock_until_executor_finishes(self):
        pending = self.hass.loop.create_future()
        # Unwrap the admin and async-response decorators to control cancellation.
        raw_handler = self.handler.__wrapped__.__wrapped__
        with patch.object(self.hass.config_entries, 'async_get_entry', return_value=self.entry), \
             patch.object(self.hass, 'async_add_executor_job', return_value=pending) as executor:
            task = asyncio.create_task(raw_handler(self.hass, self.connection, self.message))
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            await raw_handler(self.hass, self.connection, {**self.message, 'id': 2})
            self.connection.send_error.assert_called_once_with(2, 'busy', 'Wireless history request is already running')
            executor.assert_called_once()
            self.assertFalse(task.done())
            pending.set_result({'rows': []})
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.connection.send_result.assert_not_called()
            await raw_handler(self.hass, self.connection, {**self.message, 'id': 3})
            self.connection.send_result.assert_called_once_with(3, {'rows': []})

    async def test_admin_reads_page_and_storage_errors_are_sanitized(self):
        with patch.object(self.hass.config_entries, 'async_get_entry', return_value=self.entry):
            self.handler(self.hass, self.connection, self.schema(self.message))
            await self.hass.async_block_till_done(wait_background_tasks=True)
            self.connection.send_result.assert_called_once_with(1, {'rows': [], 'more': False})
            self.history.read_page.side_effect = WifiHistoryError('private device name')
            self.handler(self.hass, self.connection, self.schema(self.message))
            await self.hass.async_block_till_done(wait_background_tasks=True)
        self.assertNotIn('private device name', str(self.connection.send_error.call_args))
