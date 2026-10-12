"""Fault cases for the decision engine; no network or router writes."""

import unittest
from datetime import datetime, timedelta, timezone

import bootstrap  # noqa: F401
from service_route_sync.failover import (
    FailoverPolicy,
    FailoverState,
    LinkHealth,
    Wan,
    decide_failover,
)

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


class FailoverTests(unittest.TestCase):
    def decision(self, state, primary=True, secondary=True, now=NOW, **kwargs):
        return decide_failover(
            state,
            LinkHealth(primary, now),
            LinkHealth(secondary, now),
            now,
            kwargs.pop("policy", FailoverPolicy()),
            enabled=kwargs.pop("enabled", True),
            dry_run=kwargs.pop("dry_run", False),
            write_blocked=kwargs.pop("write_blocked", False),
        )

    def test_failure_waits_for_continuous_time_not_poll_count(self):
        state = FailoverState(Wan.PRIMARY)
        first = self.decision(state, primary=False)
        self.assertFalse(first.write_allowed)
        repeated = self.decision(first.state, primary=False)
        self.assertFalse(repeated.write_allowed)
        ready = self.decision(first.state, primary=False, now=NOW + timedelta(seconds=15))
        self.assertTrue(ready.write_allowed)
        self.assertEqual(ready.desired, Wan.SECONDARY)
        self.assertEqual(ready.state.active, Wan.PRIMARY)

    def test_unknown_health_resets_continuity(self):
        pending = self.decision(FailoverState(Wan.PRIMARY), primary=False)
        blocked = self.decision(pending.state, primary=None)
        self.assertIsNone(blocked.state.candidate_since)
        restarted = self.decision(blocked.state, primary=False, now=NOW + timedelta(seconds=60))
        self.assertFalse(restarted.write_allowed)

    def test_recovery_uses_longer_delay(self):
        first = self.decision(FailoverState(Wan.SECONDARY))
        middle = self.decision(first.state, now=NOW + timedelta(seconds=15))
        almost = self.decision(middle.state, now=NOW + timedelta(seconds=29))
        self.assertFalse(almost.write_allowed)
        self.assertTrue(self.decision(almost.state, now=NOW + timedelta(seconds=30)).write_allowed)

    def test_dry_run_and_write_latch_prevent_changes(self):
        state = FailoverState(Wan.PRIMARY, Wan.SECONDARY, NOW - timedelta(seconds=60), NOW)
        for mode in ({"dry_run": True}, {"write_blocked": True}, {"enabled": False}):
            self.assertFalse(self.decision(state, primary=False, **mode).write_allowed)

    def test_gap_without_decisions_resets_hysteresis(self):
        first = self.decision(FailoverState(Wan.PRIMARY), primary=False)
        after_gap = self.decision(first.state, primary=False, now=NOW + timedelta(seconds=60))
        self.assertFalse(after_gap.write_allowed)
        self.assertEqual(after_gap.state.candidate_since, NOW + timedelta(seconds=60))

    def test_invalid_health_values_cannot_become_truthy_success(self):
        for value in (1, 0, "true", "false", [], {}):
            with self.assertRaises(ValueError):
                LinkHealth(value, NOW)

    def test_stale_health_blocks_even_if_true(self):
        result = decide_failover(
            FailoverState(Wan.SECONDARY),
            LinkHealth(True, NOW),
            LinkHealth(True, NOW),
            NOW + timedelta(seconds=21),
            FailoverPolicy(),
            enabled=True,
            dry_run=False,
            write_blocked=False,
        )
        self.assertEqual(result.reason, "unknown_health")
        self.assertFalse(result.write_allowed)

    def test_unverified_router_state_never_initializes_itself(self):
        result = self.decision(FailoverState())
        self.assertFalse(result.write_allowed)
        self.assertEqual(result.reason, "unknown_router_state")

    def test_future_hysteresis_timestamp_cannot_trigger_write(self):
        state = FailoverState(Wan.PRIMARY, Wan.SECONDARY, NOW + timedelta(days=1))
        self.assertFalse(self.decision(state, primary=False).write_allowed)

    def test_explicit_secondary_preference_does_not_invert_silently(self):
        policy = FailoverPolicy(preference=Wan.SECONDARY)
        result = self.decision(FailoverState(Wan.SECONDARY), policy=policy)
        self.assertEqual(result.reason, "current")
        self.assertEqual(result.desired, Wan.SECONDARY)

    def test_invalid_policy_is_rejected(self):
        with self.assertRaises(ValueError):
            FailoverPolicy(maximum_age=timedelta(0))
