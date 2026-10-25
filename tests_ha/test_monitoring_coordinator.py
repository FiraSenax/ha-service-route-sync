from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from custom_components.service_route_sync.monitoring.monitoring_coordinator import (
    MonitoringCoordinator,
    TimedObservation,
)
from custom_components.service_route_sync.monitoring.monitoring_store import (
    MonitoringCheckpointStore,
)
from custom_components.service_route_sync.monitoring.monitoring_transaction import (
    MonitoringTransaction,
)
from custom_components.service_route_sync.windows.windows_archive import DailyCursor
from custom_components.service_route_sync.windows.windows_source import WindowsBatch


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.store = MonitoringCheckpointStore(self.hass, "coordinator")
        await self.store.async_commit(
            {
                "engine": {},
                "cursor": {"day": "2026-10-12", "offset": 10},
                "outbox": [],
                "tracked_port": None,
                "deferred_events": [],
            },
            expected_revision=0,
        )
        self.now = dt_util.utcnow().timestamp()
        self.readers = {
            name: AsyncMock(return_value=TimedObservation({}, self.now))
            for name in ("api", "probe", "dns")
        }
        self.readers["windows"] = AsyncMock(
            return_value=WindowsBatch(
                DailyCursor("2026-10-12", 20), (), None, False, False, 0, False
            )
        )
        self.notifier = AsyncMock()
        self.coordinator = MonitoringCoordinator(
            self.hass, None, MonitoringTransaction(self.store), self.notifier, self.readers
        )

    async def asyncTearDown(self):
        await self.coordinator.async_stop()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_stale_future_sources_are_unknown_and_do_not_supply_port_data(self):
        self.readers["api"].return_value = TimedObservation(
            {"port": {"up": True, "speed": 2500}}, self.now - 46
        )
        self.readers["probe"].return_value = TimedObservation({"lan": {"loss": 0}}, self.now + 60)
        result = await self.coordinator._async_update_data()
        self.assertFalse(result["sources"]["api"])
        self.assertFalse(result["sources"]["probe"])
        self.assertNotIn("port", self.store.state["engine"])
        self.assertEqual(self.store.state["cursor"]["offset"], 20)

    async def test_windows_failure_keeps_cursor_while_other_sources_continue(self):
        self.readers["windows"].side_effect = OSError("synthetic")
        result = await self.coordinator._async_update_data()
        self.assertFalse(result["sources"]["windows"])
        self.assertTrue(result["sources"]["api"])
        self.assertEqual(self.store.state["cursor"]["offset"], 10)

    async def test_notification_failure_does_not_discard_committed_observation(self):
        self.notifier.async_drain.side_effect = HomeAssistantError("synthetic")
        result = await self.coordinator._async_update_data()
        self.assertFalse(result["notification_ok"])
        self.assertEqual(self.store.state["cursor"]["offset"], 20)
