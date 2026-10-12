"""A failed or uncertain actor call leaves HA's durable write latch set."""

from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.service_route_sync.backends.gateway_ssh import GatewayTransportError
from custom_components.service_route_sync.backends.ipv6_actor_ssh import ActorResult
from custom_components.service_route_sync.failover import FailoverState, Wan
from custom_components.service_route_sync.ipv6_coordinator import IPv6Coordinator, reconciliation_allowed
from custom_components.service_route_sync.ipv6_control import IPv6Controls
from test_ipv6_control import NOW, observation, status


class IPv6CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def test_manual_reconciliation_requires_exact_complete_gateway_state(self):
        from dataclasses import replace
        saved = {"phase": "intent", "transaction_id": "a" * 32,
                 "digest": "a" * 64, "target": "starlink"}
        current = replace(status("starlink"), digest="b" * 64)
        self.assertTrue(reconciliation_allowed(saved, current, "b" * 64, "starlink"))
        self.assertFalse(reconciliation_allowed(saved, current, "a" * 64, "starlink"))
        self.assertFalse(reconciliation_allowed(saved, replace(current, review_required=True),
                                                "b" * 64, "starlink"))
        self.assertFalse(reconciliation_allowed(saved, replace(current, mode="dsl"),
                                                "b" * 64, "dsl"))

    def coordinator(self):
        result = object.__new__(IPv6Coordinator)
        result.hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=None))
        result.gateway = SimpleNamespace(data=observation(False, True), last_update_success=True)
        result.transport = SimpleNamespace(validate_files=lambda: None)
        result.failover_state = FailoverState(
            Wan.PRIMARY, Wan.SECONDARY, NOW - timedelta(seconds=16), NOW - timedelta(seconds=1))
        result.shadow_state = FailoverState()
        result.local_latch = False
        result.active_writer = True
        result._initialized = True
        result._controls = lambda: IPv6Controls(True, False, True, True, "auto")
        result._durable_save = AsyncMock()
        return result

    async def test_success_requires_intent_apply_independent_readback_complete(self):
        coordinator = self.coordinator()
        initial = status("dsl")
        changed = status("starlink")
        changed = __import__("dataclasses").replace(changed, digest="b" * 64)
        with (patch("custom_components.service_route_sync.ipv6_coordinator.inspect_actor",
                    new=AsyncMock(side_effect=[initial, changed])) as inspect,
              patch("custom_components.service_route_sync.ipv6_coordinator.apply_actor",
                    new=AsyncMock(return_value=ActorResult("starlink", True, "b" * 64, False))) as apply,
              patch("custom_components.service_route_sync.ipv6_coordinator.dt_util.utcnow", return_value=NOW)):
            result = await coordinator._async_update_data()
        self.assertEqual(inspect.await_count, 2)
        apply.assert_awaited_once()
        self.assertFalse(coordinator.local_latch)
        self.assertEqual(result.actor.mode, "starlink")
        self.assertEqual([call.args[0]["phase"] for call in coordinator._durable_save.await_args_list],
                         ["intent", "complete"])
        self.assertEqual(coordinator._durable_save.await_args_list[0].args[0]["target"], "starlink")

    async def test_ssh_timeout_retains_intent_and_latch(self):
        coordinator = self.coordinator()
        with (patch("custom_components.service_route_sync.ipv6_coordinator.inspect_actor",
                    new=AsyncMock(return_value=status("dsl"))),
              patch("custom_components.service_route_sync.ipv6_coordinator.apply_actor",
                    new=AsyncMock(side_effect=GatewayTransportError("timeout"))),
              patch("custom_components.service_route_sync.ipv6_coordinator.dt_util.utcnow", return_value=NOW)):
            with self.assertRaises(UpdateFailed):
                await coordinator._async_update_data()
        self.assertTrue(coordinator.local_latch)
        self.assertEqual(coordinator._durable_save.await_count, 1)
        self.assertEqual(coordinator._durable_save.await_args.args[0]["phase"], "intent")


if __name__ == "__main__":
    unittest.main()
