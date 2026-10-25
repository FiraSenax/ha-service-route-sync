"""Fault injection for every managed gateway mutation boundary."""

from contextlib import ExitStack
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from gateway import ipv6_actor as actor
from gateway.ipv6_policy import (
    PolicyError,
    PolicySnapshot,
    classify,
    parse_config,
    normalized_policy,
)
from tests_gateway.test_ipv6_policy import RAW, PD, snapshot as healthy_snapshot


class Router:
    def __init__(self, initial, *, fail_at=None):
        self.state = initial
        self.fail_at = fail_at
        self.writes = 0
        self.journal = []

    def snapshot(self, _config):
        return self.state

    def store(self, value):
        self.journal.append(value)

    def command(self, args, **_kwargs):
        self.writes += 1
        if self.writes == self.fail_at:
            raise PolicyError("injected_write_failure")
        if args[0] == "ip":
            assert args[:3] == ["ip", "-6", "rule"]
            action, priority, source, table = args[3], int(args[5]), args[7], args[9]
            assert args[4] == "pref" and args[6] == "from" and args[8] == "lookup"
            name = "policy" if priority == 31010 else "temporary_policy"
            value = f"{priority}: from {source} lookup {table}"
            rules = list(getattr(self.state, name))
            if action == "add":
                rules.append(value)
            else:
                rules.remove(value)
            self.state = replace(self.state, **{name: tuple(rules)})
            return "", 0
        assert args[0] in ("ip6tables", "iptables")
        table = args[4]
        name = "nat_chain" if table == "nat" else "sab_chain"
        action = args[5]
        chain = args[6]
        if chain.startswith("UBIOS_"):
            name = "nat_hook" if table == "nat" else "sab_hook"
        rules = list(getattr(self.state, name))
        if action == "-N":
            assert not rules
            rules = [f"-N {chain}"]
        elif action == "-F":
            assert rules == [f"-N {chain}"] or len(rules) > 1
            rules = rules[:1]
        elif action == "-X":
            assert rules == [f"-N {chain}"]
            rules = []
        else:
            position = 7 if action == "-I" else None
            suffix = args[8:] if position is not None else args[7:]
            if position is not None:
                assert args[7] == "1"
            line = " ".join(["-A", chain, *suffix])
            if action == "-D":
                rules.remove(line)
            elif action == "-I":
                rules.insert(0 if name.endswith("hook") else 1, line)
            else:
                rules.append(line)
        self.state = replace(self.state, **{name: tuple(rules)})
        return "", 0


def run(initial, desired, *, fail_at=None):
    config = parse_config(RAW)
    router = Router(initial, fail_at=fail_at)
    call = {"transaction_id": "a" * 32, "expected_digest": initial.digest(), "mode": desired}
    with ExitStack() as stack:
        stack.enter_context(patch.object(actor, "command", router.command))
        stack.enter_context(patch.object(actor, "snapshot", router.snapshot))
        stack.enter_context(patch.object(actor, "atomic_journal", router.store))
        stack.enter_context(patch.object(actor, "default_route", return_value=True))
        try:
            result = actor.apply(config, call, initial, None)
            return router, result, None
        except actor.PolicyError as error:
            return router, None, str(error)


class ActorTests(TestCase):
    def test_insert_index_after_chain(self):
        self.assertEqual(
            actor.insert_rule("ip6tables", "nat", "-A IOB_V6_NAT -j MASQUERADE"),
            ["ip6tables", "-w", "5", "-t", "nat", "-I", "IOB_V6_NAT", "1", "-j", "MASQUERADE"],
        )

    def test_exact_switch_and_all_single_command_failures_rollback(self):
        before = healthy_snapshot("primary")
        complete, result, error = run(before, "secondary")
        self.assertIsNone(error)
        self.assertTrue(result["applied"])
        self.assertEqual(classify(complete.state, parse_config(RAW)), "secondary")
        for failure in range(1, complete.writes + 1):
            with self.subTest(failure=failure):
                router, result, error = run(before, "secondary", fail_at=failure)
                self.assertEqual(error, "apply_rolled_back")
                self.assertEqual(router.state, before)
                self.assertEqual(router.journal[-1]["phase"], "complete")
                self.assertTrue(router.journal[-1]["rolled_back"])

    def test_rotated_prefix_and_all_single_command_failures_rollback(self):
        before = replace(healthy_snapshot("primary"), delegation="2001:4860:abcd:3400::/56")
        complete, result, error = run(before, "primary")
        self.assertIsNone(error)
        self.assertTrue(result["applied"])
        self.assertEqual(classify(complete.state, parse_config(RAW)), "primary")
        for failure in range(1, complete.writes + 1):
            with self.subTest(failure=failure):
                router, _, error = run(before, "primary", fail_at=failure)
                self.assertEqual(error, "apply_rolled_back")
                self.assertEqual(router.state, before)

    def test_reverse_switch_and_hook_repair(self):
        config = parse_config(RAW)
        before = healthy_snapshot("secondary")
        router, result, error = run(before, "primary")
        self.assertIsNone(error)
        self.assertEqual(classify(router.state, config), "primary")
        for failure in range(1, router.writes + 1):
            with self.subTest(failure=failure):
                failed, _, error = run(before, "primary", fail_at=failure)
                self.assertEqual(error, "apply_rolled_back")
                self.assertEqual(failed.state, before)
        missing = replace(healthy_snapshot("primary"), nat_hook=())
        repaired, result, error = run(missing, "primary")
        self.assertIsNone(error)
        self.assertEqual(classify(repaired.state, config), "primary")
        for failure in range(1, repaired.writes + 1):
            with self.subTest(failure=failure):
                failed, _, error = run(missing, "primary", fail_at=failure)
                self.assertEqual(error, "apply_rolled_back")
                self.assertEqual(failed.state, missing)

    def test_blank_rebuild_and_all_single_command_failures_rollback(self):
        before = PolicySnapshot((), (), (), (), (), (), PD)
        complete, result, error = run(before, "primary")
        self.assertIsNone(error)
        self.assertTrue(result["applied"])
        self.assertEqual(classify(complete.state, parse_config(RAW)), "primary")
        for failure in range(1, complete.writes + 1):
            with self.subTest(failure=failure):
                router, _, error = run(before, "primary", fail_at=failure)
                self.assertEqual(error, "apply_rolled_back")
                self.assertEqual(router.state, before)

    def test_unknown_state_and_stale_digest_never_write(self):
        before = healthy_snapshot("primary")
        extra = replace(before, nat_chain=before.nat_chain + ("-A IOB_V6_NAT -j ACCEPT",))
        router, _, error = run(extra, "secondary")
        self.assertEqual(error, "unknown_managed_state")
        self.assertEqual(router.writes, 0)
        config = parse_config(RAW)
        call = {"transaction_id": "a" * 32, "expected_digest": "0" * 64, "mode": "secondary"}
        with patch.object(actor, "command", side_effect=AssertionError("wrote")):
            with self.assertRaisesRegex(actor.PolicyError, "state_changed"):
                actor.apply(config, call, before, None)

    def test_policy_parser_refuses_extra_selectors(self):
        with self.assertRaises(PolicyError):
            normalized_policy(
                ["31010: from fd12:3456:789a:bc00::/56 lookup 201.ppp0 fwmark 0x1"], 31010
            )

    def test_concurrent_policy_edit_latches_without_overwriting_it(self):
        before = healthy_snapshot("primary")
        config = parse_config(RAW)
        router = Router(before)
        original_command = router.command
        foreign = "31010: from fd00:ffff::/56 lookup 201.ppp0"

        def concurrent_command(args, **kwargs):
            if router.writes == 2:
                router.state = replace(router.state, policy=router.state.policy + (foreign,))
                raise actor.PolicyError("injected_write_failure")
            return original_command(args, **kwargs)

        call = {"transaction_id": "b" * 32, "expected_digest": before.digest(), "mode": "secondary"}
        with (
            patch.object(actor, "command", concurrent_command),
            patch.object(actor, "snapshot", router.snapshot),
            patch.object(actor, "atomic_journal", router.store),
            patch.object(actor, "default_route", return_value=True),
        ):
            with self.assertRaisesRegex(actor.PolicyError, "review_required"):
                actor.apply(config, call, before, None)
        self.assertIn(foreign, router.state.policy)
        self.assertEqual(router.journal[-1]["phase"], "blocked")

    def test_changed_delegation_blocks_stale_rollback(self):
        before = healthy_snapshot("primary")
        current = replace(before, delegation="2001:4860:abcd:3500::/56")
        with patch.object(actor, "command", side_effect=AssertionError("must not write")):
            with self.assertRaisesRegex(actor.PolicyError, "rollback_state_ambiguous"):
                actor.restore(parse_config(RAW), before, current, "secondary")
