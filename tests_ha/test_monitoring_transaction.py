from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from custom_components.service_route_sync.monitoring.monitoring_store import (
    MonitoringCheckpointStore,
)
from custom_components.service_route_sync.monitoring.monitoring_transaction import (
    MonitoringTransaction,
)
from custom_components.service_route_sync.windows.windows_cursor import AlertCursor
from custom_components.service_route_sync.windows.windows_source import WindowsBatch
from custom_components.service_route_sync.windows.windows_archive import DailyCursor
from custom_components.service_route_sync.monitoring.monitoring_history import MonitoringHistory


class TransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_mode_records_without_queuing_and_optional_windows_has_no_alarm(self):
        transaction = MonitoringTransaction(
            self.store, notifications_enabled=False, windows_enabled=False
        )
        await transaction.async_sample(self.active_with_failed_probe(), self.batch(), self.now)
        await transaction.async_sample(
            self.active_with_failed_probe(), self.batch(20), self.now + 121
        )
        self.assertEqual(self.store.state["outbox"], [])
        self.assertTrue(self.store.state["engine"]["incidents"]["Messquelle probe"]["active"])
        self.assertNotIn("Messquelle windows", self.store.state["engine"]["incidents"])

    async def test_history_prepare_then_store_failure_is_invisible_after_restart(self):
        history = MonitoringHistory(Path(self.directory.name) / "history" / "events.sqlite")
        transaction = MonitoringTransaction(self.store, history)
        await transaction.async_sample(self.active_with_failed_probe(), self.batch(), self.now)
        with patch.object(
            self.store.store, "async_save", AsyncMock(side_effect=OSError("synthetic"))
        ):
            with self.assertRaises(OSError):
                await transaction.async_sample(
                    self.active_with_failed_probe(), self.batch(20), self.now + 121
                )
        fresh = MonitoringCheckpointStore(self.hass, "transaction")
        await fresh.async_load()
        self.assertEqual(fresh.state["cursor"]["offset"], 10)
        self.assertEqual(
            history.snapshot(fresh.state["history_revision"], self.now + 121)["counts"], {}
        )
        restored = MonitoringTransaction(fresh, history)
        await restored.async_sample(self.active_with_failed_probe(), self.batch(30), self.now + 122)
        result = history.snapshot(fresh.state["history_revision"], self.now + 122)
        self.assertEqual(result["counts"], {"Messquelle probe": 1})
        self.assertEqual(len(fresh.state["outbox"]), 1)
        marker = fresh.state["history_revision"]
        await restored.async_acknowledge(fresh.state["outbox"][0]["delivery_id"])
        self.assertEqual(fresh.state["history_revision"], marker)

    async def test_history_failure_never_advances_accepted_cursor(self):
        history = MonitoringHistory(Path(self.directory.name) / "history" / "events.sqlite")
        transaction = MonitoringTransaction(self.store, history)
        await transaction.async_sample({}, self.batch(), self.now)
        before = self.store.state
        with patch.object(history, "prepare", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                await transaction.async_sample({}, self.batch(20), self.now + 5)
        self.assertEqual(self.store.state, before)

    async def test_daily_cursor_and_pending_future_event_restore_together(self):
        batch = self.batch(events=(self.event,))
        batch = WindowsBatch(
            DailyCursor("2026-10-12", 25),
            batch.events,
            batch.agent,
            batch.heartbeat_ok,
            False,
            0,
            False,
        )
        await self.transaction.async_sample({}, batch, self.now)
        fresh = MonitoringCheckpointStore(self.hass, "transaction")
        await fresh.async_load()
        self.assertEqual(fresh.state["cursor"], {"day": "2026-10-12", "offset": 25})
        self.assertFalse(fresh.state["deferred_events"][0]["delivered"])

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.store = MonitoringCheckpointStore(self.hass, "transaction")
        self.transaction = MonitoringTransaction(self.store)
        self.now = 1791763200
        self.event = {
            "time": "2026-10-12T00:01:00Z",
            "provider": "User32",
            "id": "1074",
            "level": "4",
            "message": "synthetic",
        }

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    def active_with_failed_probe(self):
        return {
            "api_ok": True,
            "probe_ok": False,
            "port": {"up": True, "speed": 1000},
            "probes": {"lan": {"loss": 0, "avg": 1}},
        }

    def batch(self, offset=10, events=()):
        return WindowsBatch(AlertCursor(1, offset), events, None, True, False, 0, False)

    async def test_future_event_is_committed_with_cursor_then_consumed_after_restore(self):
        await self.transaction.async_sample({}, self.batch(events=(self.event,)), self.now)
        fresh = MonitoringCheckpointStore(self.hass, "transaction")
        await fresh.async_load()
        self.assertEqual(fresh.state["cursor"]["offset"], 10)
        self.assertNotIn("power_off", fresh.state["engine"])
        await MonitoringTransaction(fresh).async_sample({}, self.batch(20), self.now + 60)
        self.assertEqual(fresh.state["engine"]["power_off"], self.now + 60)
        self.assertTrue(fresh.state["deferred_events"][0]["delivered"])

    async def test_failed_save_keeps_engine_cursor_and_outbox_unchanged(self):
        await self.transaction.async_sample({}, self.batch(), self.now)
        before = self.store.state
        with patch.object(
            self.store.store, "async_save", AsyncMock(side_effect=OSError("synthetic"))
        ):
            with self.assertRaises(OSError):
                await self.transaction.async_sample(
                    {}, self.batch(20, (self.event,)), self.now + 60
                )
        self.assertEqual(self.store.state, before)

    async def test_incident_outbox_persists_until_explicit_ack(self):
        row = self.active_with_failed_probe()
        await self.transaction.async_sample(row, self.batch(), self.now)
        await self.transaction.async_sample(row, self.batch(20), self.now + 121)
        pending = self.store.state["outbox"]
        self.assertEqual(len(pending), 1)
        fresh = MonitoringCheckpointStore(self.hass, "transaction")
        await fresh.async_load()
        restored = MonitoringTransaction(fresh)
        self.assertEqual(fresh.state["outbox"], pending)
        self.assertTrue(await restored.async_acknowledge(pending[0]["delivery_id"]))
        self.assertFalse(await restored.async_acknowledge(pending[0]["delivery_id"]))
        self.assertEqual(fresh.state["outbox"], [])
