import copy
import unittest

import bootstrap  # noqa: F401
from service_route_sync.core import (
    SyncError,
    apply_plan,
    build_plan,
    normalize_prefixes,
    restore_backup,
)


def route():
    return {
        "_id": "test-route",
        "description": "Example",
        "matching_target": "IP",
        "enabled": False,
        "network_id": "example-wan",
        "target_devices": [{"type": "ALL_CLIENTS"}],
        "kill_switch_enabled": True,
        "ip_ranges": [{"start": "8.8.4.4", "stop": "8.8.4.4"}],
        "domains": [],
        "regions": [],
        "ip_addresses": [
            {"ip_or_subnet": "8.8.8.0/24", "ip_version": "v4", "ports": [], "port_ranges": []}
        ],
    }


class FakeBackend:
    def __init__(self, initial):
        self.routes = {initial["_id"]: copy.deepcopy(initial)}
        self.events = []
        self.ignore_write = False

    async def read_routes(self):
        return copy.deepcopy(self.routes)

    async def write_route(self, value):
        self.events.append("write")
        if not self.ignore_write:
            self.routes[value["_id"]] = copy.deepcopy(value)


class PrefixTests(unittest.TestCase):
    def test_fortnite_accepts_any_existing_anchor_ranges_and_preserves_them(self):
        for ranges in ([], [{"ip_version": "v4", "ip_start": "9.9.9.1", "ip_stop": "9.9.9.2"}]):
            original = route()
            original["ip_ranges"] = ranges
            plan = build_plan("fortnite", original, ["8.8.8.0/24", "1.1.1.0/24"])
            self.assertEqual(plan.after["ip_ranges"], ranges)
            self.assertEqual(original["ip_ranges"], ranges)

    def test_invalid_empty_private_and_default_inputs_fail(self):
        for values in (
            [],
            ["garbage"],
            ["0.0.0.0/0"],
            ["::/0"],
            ["192.168.0.0/16"],
            ["224.0.0.0/4"],
            ["ff00::/8"],
            ["127.0.0.1"],
        ):
            with self.subTest(values=values), self.assertRaises(SyncError):
                normalize_prefixes(values)

    def test_collapse_preserves_coverage_and_limits_never_truncate(self):
        self.assertEqual(normalize_prefixes(["8.8.8.0/25", "8.8.8.128/25"]), ("8.8.8.0/24",))
        with self.assertRaises(SyncError):
            normalize_prefixes(["8.8.8.0/24", "9.9.9.0/24"], maximum=1)

    def test_removal_guard_uses_address_coverage_across_collapsed_cidrs(self):
        old = route()
        old["ip_addresses"] = [
            {"ip_or_subnet": value, "ip_version": "v4", "ports": [], "port_ranges": []}
            for value in ("8.8.8.0/25", "8.8.8.128/25")
        ]
        collapsed = build_plan("example", old, ["8.8.8.0/24"])
        self.assertEqual(collapsed.removed, 2)
        self.assertEqual(collapsed.removed_coverage_pct, 0)
        self.assertFalse(collapsed.changed)
        with self.assertRaises(SyncError):
            build_plan("example", old, ["8.8.8.0/26"])

    def test_ipv6_disabled_must_not_make_empty_update(self):
        with self.assertRaises(SyncError):
            normalize_prefixes(["2606:4700::/32"], ipv6=False)

    def test_preserves_wan_scope_disabled_state_and_anchors(self):
        before = route()
        plan = build_plan("example", before, ["8.8.8.0/24", "9.9.9.0/24"])
        for key, value in before.items():
            if key != "ip_addresses":
                self.assertEqual(plan.after[key], value)
        self.assertEqual(before, route())
        self.assertEqual((plan.added, plan.removed), (1, 0))

    def test_removal_and_port_matching_require_review(self):
        with self.assertRaises(SyncError):
            build_plan("example", route(), ["9.9.9.0/24"])
        filtered = route()
        filtered["ip_addresses"][0]["ports"] = [443]
        with self.assertRaises(SyncError):
            build_plan("example", filtered, ["8.8.8.0/24"])

    def test_fortnite_requires_existing_epic_anchor_ranges(self):
        existing = route()
        existing["ip_ranges"] = [
            {"ip_version": "v4", "ip_start": "199.255.40.0", "ip_stop": "199.255.43.255"},
            {"ip_version": "v4", "ip_start": "185.25.180.0", "ip_stop": "185.25.183.255"},
        ]
        plan = build_plan("fortnite", existing, ["8.8.8.0/24", "52.23.121.216/32"])
        self.assertEqual(plan.after["ip_ranges"], existing["ip_ranges"])
        existing["ip_ranges"].pop()
        with self.assertRaises(SyncError):
            build_plan("fortnite", existing, ["52.23.121.216/32"])


class TransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_backup_is_saved_before_write_and_result_confirmed(self):
        backend = FakeBackend(route())
        plan = build_plan("example", route(), ["8.8.8.0/24", "9.9.9.0/24"])

        async def backup(value):
            backend.events.append("backup")

        await apply_plan(backend, plan, backup)
        self.assertEqual(backend.events, ["backup", "write"])
        self.assertEqual(backend.routes["test-route"], plan.after)

    async def test_concurrent_scope_change_prevents_write(self):
        backend = FakeBackend(route())
        plan = build_plan("example", route(), ["8.8.8.0/24", "9.9.9.0/24"])
        backend.routes["test-route"]["network_id"] = "different-wan"

        async def backup(value):
            self.fail("Must not back up or write a stale plan")

        with self.assertRaises(SyncError):
            await apply_plan(backend, plan, backup)
        self.assertEqual(backend.events, [])

    async def test_unconfirmed_write_is_not_retried(self):
        backend = FakeBackend(route())
        backend.ignore_write = True
        plan = build_plan("example", route(), ["8.8.8.0/24", "9.9.9.0/24"])

        async def backup(value):
            pass

        with self.assertRaises(SyncError):
            await apply_plan(backend, plan, backup)
        self.assertEqual(backend.events, ["write"])

    async def test_restore_refuses_later_changes(self):
        before = route()
        plan = build_plan("example", before, ["8.8.8.0/24", "9.9.9.0/24"])
        backend = FakeBackend(plan.after)
        snapshot = {"route_id": plan.route_id, "before": before, "after": plan.after}
        backend.routes["test-route"]["enabled"] = True
        with self.assertRaises(SyncError):
            await restore_backup(backend, snapshot)
        self.assertEqual(backend.events, [])

    async def test_restore_returns_original_prefixes(self):
        before = route()
        plan = build_plan("example", before, ["8.8.8.0/24", "9.9.9.0/24"])
        backend = FakeBackend(plan.after)
        await restore_backup(
            backend, {"route_id": plan.route_id, "before": before, "after": plan.after}
        )
        self.assertEqual(backend.routes["test-route"], before)


class BatchTests(unittest.TestCase):
    def test_later_invalid_route_prevents_a_complete_plan(self):
        from service_route_sync.core import build_plans

        routes = {"test-route": route()}
        with self.assertRaises(SyncError):
            build_plans(
                routes,
                {"one": ["8.8.8.0/24"], "two": ["9.9.9.0/24"]},
                {"one": "test-route", "two": "missing"},
            )
        self.assertEqual(routes["test-route"], route())

    def test_two_services_cannot_overwrite_the_same_route(self):
        from service_route_sync.core import build_plans

        with self.assertRaises(SyncError):
            build_plans(
                {"test-route": route()},
                {"one": ["8.8.8.0/24"], "two": ["9.9.9.0/24"]},
                {"one": "test-route", "two": "test-route"},
            )

    def test_large_removals_visible_in_preview_but_blocked_for_writes(self):
        from service_route_sync.core import build_plans

        args = ({"test-route": route()}, {"one": ["9.9.9.0/24"]}, {"one": "test-route"})
        self.assertEqual(build_plans(*args)[0].removed, 1)
        with self.assertRaises(SyncError):
            build_plans(*args, maximum_removal=0.5)
