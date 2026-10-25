"""The HA planner never guesses router ownership or WAN health."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from ipaddress import IPv6Network
import unittest

from custom_components.service_route_sync.backends.ipv6_actor_ssh import ActorStatus
from custom_components.service_route_sync.network.failover import FailoverState, Wan
from custom_components.service_route_sync.network.gateway_observation import (
    GatewayObservation,
    ProtocolObservation,
)
from custom_components.service_route_sync.ipv6.ipv6_control import IPv6Controls, plan_ipv6


NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
CONTROLS = IPv6Controls(True, False, True, True, "auto")


def status(mode="primary", *, owned_mode=None, blank=False, repairable=True, review=False):
    return ActorStatus(
        mode,
        True,
        owned_mode if owned_mode is not None else mode,
        repairable,
        blank,
        mode in ("primary", "secondary"),
        "a" * 64,
        True,
        True,
        True,
        review,
    )


def observation(primary=True, secondary=True, *, age=0):
    when = NOW - timedelta(seconds=age)
    primary = ProtocolObservation(
        "ppp0",
        when,
        True,
        True,
        primary,
        True,
        True,
        True,
        None if not primary else IPv6Network("2001:4860:abcd:1200::/56"),
    )
    secondary = ProtocolObservation("eth1", when, True, True, secondary, True, True, True, None)
    return GatewayObservation(when, primary, secondary)


class IPv6ControlTests(unittest.TestCase):
    def test_no_write_without_explicit_gates_or_known_state(self):
        for changed in (
            IPv6Controls(False, False, True, True, "auto"),
            IPv6Controls(True, False, False, True, "auto"),
            IPv6Controls(True, False, True, False, "auto"),
        ):
            self.assertFalse(
                plan_ipv6(FailoverState(Wan.PRIMARY), status(), observation(), changed, NOW).write
            )
        for row in (
            status("unknown", owned_mode="unknown", repairable=False),
            status("unknown", owned_mode="unknown", review=True),
        ):
            self.assertFalse(plan_ipv6(FailoverState(), row, observation(), CONTROLS, NOW).write)

    def test_primary_outage_has_elapsed_hysteresis_and_no_both_down_write(self):
        starting = FailoverState(Wan.PRIMARY)
        first = plan_ipv6(starting, status(), observation(False, True), CONTROLS, NOW)
        self.assertEqual(first.reason, "stabilizing")
        self.assertFalse(first.write)
        second = plan_ipv6(
            first.state, status(), observation(False, True), CONTROLS, NOW + timedelta(seconds=21)
        )
        self.assertFalse(second.write)  # The old sample expired.
        refreshed = observation(False, True)
        later = replace(
            refreshed,
            observed_at=NOW + timedelta(seconds=16),
            primary=replace(refreshed.primary, observed_at=NOW + timedelta(seconds=16)),
            secondary=replace(refreshed.secondary, observed_at=NOW + timedelta(seconds=16)),
        )
        second = plan_ipv6(first.state, status(), later, CONTROLS, NOW + timedelta(seconds=16))
        self.assertTrue(second.write)
        self.assertEqual(second.target, "secondary")
        self.assertFalse(
            plan_ipv6(first.state, status(), observation(False, False), CONTROLS, NOW).write
        )

    def test_prefix_rotation_repairs_only_with_healthy_primary(self):
        stale = status("unknown", owned_mode="primary")
        plan = plan_ipv6(FailoverState(Wan.PRIMARY), stale, observation(), CONTROLS, NOW)
        self.assertTrue(plan.write)
        self.assertEqual(plan.reason, "repair_prefix")
        self.assertFalse(
            plan_ipv6(
                FailoverState(Wan.PRIMARY), stale, observation(False, True), CONTROLS, NOW
            ).write
        )

    def test_blank_router_bootstraps_after_new_continuous_samples(self):
        blank = status("unknown", owned_mode="unknown", blank=True)
        first = plan_ipv6(FailoverState(), blank, observation(), CONTROLS, NOW)
        self.assertFalse(first.write)
        sample = observation()
        later = replace(
            sample,
            observed_at=NOW + timedelta(seconds=16),
            primary=replace(sample.primary, observed_at=NOW + timedelta(seconds=16)),
            secondary=replace(sample.secondary, observed_at=NOW + timedelta(seconds=16)),
        )
        second = plan_ipv6(first.state, blank, later, CONTROLS, NOW + timedelta(seconds=16))
        self.assertTrue(second.write)
        self.assertEqual(second.target, "primary")

    def test_stale_or_unknown_health_blocks_actuation(self):
        self.assertEqual(
            plan_ipv6(
                FailoverState(Wan.PRIMARY),
                status(),
                observation(False, True, age=21),
                CONTROLS,
                NOW,
            ).reason,
            "unknown_health",
        )
        self.assertFalse(
            plan_ipv6(
                FailoverState(Wan.PRIMARY), status(), observation(), CONTROLS, NOW, local_latch=True
            ).write
        )

    def test_read_only_actor_allows_preview_but_never_write(self):
        read_only = replace(status(), writable=False)
        self.assertEqual(
            plan_ipv6(
                FailoverState(Wan.PRIMARY), read_only, observation(False, True), CONTROLS, NOW
            ).reason,
            "actor_read_only",
        )
        preview = plan_ipv6(
            FailoverState(Wan.PRIMARY),
            read_only,
            observation(False, True),
            replace(CONTROLS, dry_run=True),
            NOW,
        )
        self.assertEqual(preview.target, "secondary")
        self.assertFalse(preview.write)


if __name__ == "__main__":
    unittest.main()
