"""Bounded Windows archive polling must not replay events between SSH reads."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from custom_components.service_route_sync.monitoring.monitoring_coordinator import (
    MonitoringCoordinator,
)
from custom_components.service_route_sync.windows.windows_archive import DailyCursor
from custom_components.service_route_sync.windows.windows_source import WindowsBatch


class WindowsPollTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_heartbeat_without_replaying_events(self):
        before = DailyCursor("2026-10-12", 100)
        after = DailyCursor("2026-10-12", 200)
        batch = WindowsBatch(after, ({"id": "1"},), {"last_keepalive": 1}, True, False, 0, False)
        source = SimpleNamespace(
            _windows_cache=None,
            _windows_observed_at=None,
            _windows_next_poll=0.0,
            _read=AsyncMock(return_value=batch),
        )
        first = await MonitoringCoordinator._read_windows(source, before, 1000)
        cached = await MonitoringCoordinator._read_windows(source, after, 1005)
        self.assertEqual(first.events, ({"id": "1"},))
        self.assertEqual(cached.events, ())
        self.assertTrue(cached.heartbeat_ok)
        self.assertEqual(source._read.await_count, 1)
        await MonitoringCoordinator._read_windows(source, after, 1060)
        self.assertEqual(source._read.await_count, 2)

    async def test_failed_source_backs_off_without_false_health(self):
        cursor = DailyCursor("2026-10-12", 100)
        source = SimpleNamespace(
            _windows_cache=None,
            _windows_observed_at=None,
            _windows_next_poll=0.0,
            _read=AsyncMock(return_value=None),
        )
        self.assertIsNone(await MonitoringCoordinator._read_windows(source, cursor, 1000))
        self.assertIsNone(await MonitoringCoordinator._read_windows(source, cursor, 1005))
        self.assertIsNone(source._windows_observed_at)
        self.assertEqual(source._read.await_count, 1)
        await MonitoringCoordinator._read_windows(source, cursor, 1060)
        self.assertEqual(source._read.await_count, 2)


if __name__ == "__main__":
    unittest.main()
