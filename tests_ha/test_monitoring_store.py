from pathlib import Path
import sys
import tempfile
import unittest
import asyncio
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from custom_components.service_route_sync.monitoring_store import (
    MonitoringCheckpointStore,
    MonitoringCheckpointError,
)


class MonitoringStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_prepare_holds_lock_until_completion_and_never_saves(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def prepare():
            started.set()
            await release.wait()

        task = asyncio.create_task(
            self.store.async_commit(self.state, expected_revision=0, before_save=prepare)
        )
        await started.wait()
        task.cancel()
        peer = MonitoringCheckpointStore(self.hass, "test")
        contender = asyncio.create_task(peer.async_commit(self.state, expected_revision=0))
        await asyncio.sleep(0)
        self.assertFalse(contender.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await contender
        self.assertEqual(self.store.revision, 0)
        self.assertEqual(peer.revision, 1)

    async def test_cancelled_save_finishes_and_accepts_revision_before_releasing_lock(self):
        started, release = asyncio.Event(), asyncio.Event()
        original = self.store.store.async_save

        async def save(data):
            started.set()
            await release.wait()
            await original(data)

        with patch.object(self.store.store, "async_save", save):
            task = asyncio.create_task(self.store.async_commit(self.state, expected_revision=0))
            await started.wait()
            task.cancel()
            peer = MonitoringCheckpointStore(self.hass, "test")
            contender = asyncio.create_task(peer.async_commit(self.state, expected_revision=0))
            await asyncio.sleep(0)
            self.assertFalse(contender.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.assertRaises(MonitoringCheckpointError):
                await contender
        self.assertEqual(self.store.revision, 1)
        self.assertEqual(self.store.state, self.state)

    async def test_other_store_instance_cannot_overwrite_newer_cursor(self):
        await self.store.async_commit(self.state, expected_revision=0)
        peer = MonitoringCheckpointStore(self.hass, "test")
        await peer.async_load()
        self.state["cursor"]["offset"] = 20
        await self.store.async_commit(self.state, expected_revision=1)
        callback = AsyncMock()
        with self.assertRaises(MonitoringCheckpointError):
            await peer.async_commit(peer.state, expected_revision=1, before_save=callback)
        callback.assert_not_awaited()
        latest = MonitoringCheckpointStore(self.hass, "test")
        self.assertEqual((await latest.async_load())["cursor"]["offset"], 20)

    async def test_unloaded_instance_cannot_blindly_replace_existing_checkpoint(self):
        await self.store.async_commit(self.state, expected_revision=0)
        fresh = MonitoringCheckpointStore(self.hass, "test")
        with self.assertRaises(MonitoringCheckpointError):
            await fresh.async_commit(self.state, expected_revision=0)

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.store = MonitoringCheckpointStore(self.hass, "test")
        self.state = {
            "engine": {"incidents": {}},
            "cursor": {"inode": 1, "offset": 10},
            "outbox": [],
            "tracked_port": None,
            "deferred_events": [],
        }

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    async def test_actual_store_restore_keeps_cursor_and_pending_events_together(self):
        self.state["outbox"] = [
            {
                "delivery_id": "a" * 64,
                "event": {"key": "test", "message": "synthetic", "time": 10, "recovery": False},
            }
        ]
        self.state["deferred_events"] = [
            {
                "event": {
                    "id": "1074",
                    "time": "2026-10-12T00:01:00Z",
                    "provider": "User32",
                    "level": "4",
                    "message": "synthetic",
                },
                "delivered": False,
            }
        ]
        await self.store.async_commit(self.state, expected_revision=0)
        fresh = MonitoringCheckpointStore(self.hass, "test")
        restored = await fresh.async_load()
        self.assertEqual(restored, self.state)
        self.assertEqual(fresh.revision, 1)
        self.state["cursor"]["offset"] = 99
        self.assertEqual(self.store.state["cursor"]["offset"], 10)

    async def test_failed_save_does_not_accept_new_cursor(self):
        await self.store.async_commit(self.state, expected_revision=0)
        self.state["cursor"]["offset"] = 20
        with patch.object(
            self.store.store, "async_save", AsyncMock(side_effect=OSError("synthetic"))
        ):
            with self.assertRaises(OSError):
                await self.store.async_commit(self.state, expected_revision=1)
        self.assertEqual(self.store.revision, 1)
        self.assertEqual(self.store.state["cursor"]["offset"], 10)

    async def test_stale_writer_and_invalid_cursor_never_overwrite_checkpoint(self):
        await self.store.async_commit(self.state, expected_revision=0)
        with self.assertRaises(MonitoringCheckpointError):
            await self.store.async_commit(self.state, expected_revision=0)
        self.state["cursor"]["offset"] = True
        with self.assertRaises(MonitoringCheckpointError):
            await self.store.async_commit(self.state, expected_revision=1)

    async def test_corrupt_nested_state_is_rejected_before_any_save(self):
        await self.store.async_commit(self.state, expected_revision=0)
        before = self.store.state
        for engine in (
            {"incidents": {"test": {"active": 1, "since": None, "good": None}}},
            {"last_pulse": {"test": float("nan")}},
            {"unexpected": True},
        ):
            candidate = dict(before, engine=engine)
            with self.assertRaises(MonitoringCheckpointError):
                await self.store.async_commit(candidate, expected_revision=1)
            self.assertEqual(self.store.state, before)


if __name__ == "__main__":
    unittest.main()
