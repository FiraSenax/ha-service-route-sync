"""A broken optional capability cannot prevent independent features from starting."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from custom_components.service_route_sync.lifecycle import Feature, prepare_features
from custom_components.service_route_sync.runtime import RuntimeData


class FeatureLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_feature_is_closed_and_dependents_skip_without_disabling_other_features(
        self,
    ):
        runtime = RuntimeData()
        broken = SimpleNamespace(
            load=AsyncMock(side_effect=ValueError()), async_shutdown=AsyncMock()
        )
        dependent = AsyncMock()
        independent = SimpleNamespace(async_shutdown=AsyncMock())
        failures = await prepare_features(
            runtime,
            [
                Feature("gateway", True, lambda: broken, initialize="load"),
                Feature("ipv6_actor", True, dependent, requires=("gateway",)),
                Feature("work_probes", True, lambda: independent),
            ],
        )
        self.assertEqual(len(failures), 2)
        self.assertIsNone(runtime.gateway)
        self.assertIsNone(runtime.ipv6_actor)
        self.assertIs(runtime.work_probes, independent)
        dependent.assert_not_called()
        broken.async_shutdown.assert_awaited_once()
        await runtime.shutdown()
        independent.async_shutdown.assert_awaited_once()
