from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from custom_components.service_route_sync.monitoring_coordinator import TimedObservation
from custom_components.service_route_sync.monitoring_probes import (
    MonitoringProbeReader,
    MonitoringPCProbeReader,
)
from types import SimpleNamespace


class ProbeCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_pc_failure_does_not_remove_wan_evidence(self):
        stamp = dt_util.utcnow().timestamp()
        dsl = AsyncMock(
            return_value=TimedObservation({"wan_cf": {"loss": 0}, "wan_google": {"loss": 0}}, stamp)
        )
        pc = AsyncMock(side_effect=OSError("synthetic"))
        value = await MonitoringProbeReader(dsl, pc)()
        self.assertEqual(set(value.value), {"wan_cf", "wan_google"})

    async def test_stale_wan_does_not_override_fresh_pc_measurements(self):
        stamp = dt_util.utcnow().timestamp()
        dsl = AsyncMock(return_value=TimedObservation({"wan_cf": {"loss": 0}}, stamp - 46))
        pc = AsyncMock(return_value=TimedObservation({"lan": {"loss": 100}}, stamp))
        value = await MonitoringProbeReader(dsl, pc)()
        self.assertEqual(set(value.value), {"lan"})

    async def test_total_failure_remains_unknown_and_scopes_cannot_mix(self):
        with self.assertRaises(HomeAssistantError):
            await MonitoringProbeReader(
                AsyncMock(side_effect=OSError()), AsyncMock(side_effect=OSError())
            )()
        with self.assertRaises(ValueError):
            MonitoringPCProbeReader(
                None, None, SimpleNamespace(profiles={"office_pc": (), "gaming": ()})
            )
