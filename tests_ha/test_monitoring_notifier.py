from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from custom_components.service_route_sync.monitoring.monitoring_store import (
    MonitoringCheckpointStore,
)
from custom_components.service_route_sync.monitoring.monitoring_transaction import (
    MonitoringTransaction,
)
from custom_components.service_route_sync.monitoring.monitoring_notifier import MonitoringNotifier


class NotifierTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.store = MonitoringCheckpointStore(self.hass, "notifytest")
        pending = [
            {
                "delivery_id": character * 64,
                "event": {"key": "test", "message": "synthetic", "time": 10, "recovery": False},
            }
            for character in ("a", "b")
        ]
        await self.store.async_commit(
            {
                "engine": {},
                "cursor": None,
                "tracked_port": None,
                "deferred_events": [],
                "outbox": pending,
            },
            expected_revision=0,
        )
        self.notifier = MonitoringNotifier(
            self.hass, MonitoringTransaction(self.store), "test_target", "Office PC"
        )

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    async def test_missing_service_preserves_pending_queue(self):
        with self.assertRaises(HomeAssistantError):
            await self.notifier.async_drain()
        self.assertEqual(len(self.store.state["outbox"]), 2)

    async def test_failed_actual_service_call_is_retried_after_store_restore(self):
        async def fail(call):
            raise HomeAssistantError("synthetic")

        self.hass.services.async_register("notify", "test_target", fail)
        with self.assertRaises(HomeAssistantError):
            await self.notifier.async_drain()
        fresh = MonitoringCheckpointStore(self.hass, "notifytest")
        await fresh.async_load()
        self.assertEqual(len(fresh.state["outbox"]), 2)
        calls = []

        async def succeed(call):
            calls.append(call.data)

        self.hass.services.async_register("notify", "test_target", succeed)
        notifier = MonitoringNotifier(
            self.hass, MonitoringTransaction(fresh), "test_target", "Office PC"
        )
        self.assertEqual(await notifier.async_drain(maximum=1), 1)
        self.assertEqual(len(fresh.state["outbox"]), 1)
        self.assertTrue(calls[0]["title"].startswith("Office PC"))
        self.assertNotIn("data", calls[0])
        self.assertEqual(await notifier.async_drain(), 1)
        self.assertEqual(fresh.state["outbox"], [])
        self.assertEqual(await notifier.async_drain(), 0)
