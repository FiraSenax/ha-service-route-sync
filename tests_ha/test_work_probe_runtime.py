from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_ha import test_network_runtime as base
from custom_components.service_route_sync.work_probe_coordinator import WorkProbeCoordinator
from custom_components.service_route_sync.work_probes import WorkProbe
from homeassistant.util import dt as dt_util


class WorkRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = base.NetworkRuntimeTests.asyncTearDown

    async def asyncSetUp(self):
        await base.NetworkRuntimeTests.asyncSetUp(self)
        await self.coordinator.async_shutdown()
        self.coordinator = WorkProbeCoordinator(self.hass, self.entry)

    async def test_fixed_targets_and_immutable_complete_snapshot(self):
        row = WorkProbe(dt_util.utcnow(), True, 302, True, 0, 0, 0, 0, 0)
        runner = AsyncMock(return_value=row)
        with patch(
            "custom_components.service_route_sync.work_probe_coordinator.probe_work_endpoint",
            runner,
        ):
            await self.coordinator.async_config_entry_first_refresh()
        self.assertEqual(set(self.coordinator.data), {"m365-login", "m365-outlook", "m365-teams"})
        self.assertEqual(runner.await_count, 3)
        with self.assertRaises(TypeError):
            self.coordinator.data["other"] = row
        self.assertTrue(self.coordinator.last_update_success)

    async def test_failed_endpoint_is_measurement_not_entire_collector_failure(self):
        async def probe(target, now):
            return WorkProbe(
                now(),
                target != "m365-login",
                0 if target == "m365-login" else 302,
                target != "m365-login",
            )

        with patch(
            "custom_components.service_route_sync.work_probe_coordinator.probe_work_endpoint",
            side_effect=probe,
        ):
            await self.coordinator.async_config_entry_first_refresh()
        self.assertFalse(self.coordinator.data["m365-login"].transport_ok)
        self.assertTrue(self.coordinator.data["m365-outlook"].transport_ok)
        self.assertTrue(self.coordinator.last_update_success)


if __name__ == "__main__":
    unittest.main()
