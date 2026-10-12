"""Exact managed-rule ownership before any gateway write."""

import unittest

from gateway.ipv6_policy import (PolicyError, PolicySnapshot, classify, owned_mode, recoverable, expected_nat,
                                 expected_policy, expected_sab, parse_config)


RAW = {
    "schema": 1,
    "ula_prefix": "fd12:3456:789a:bc00::/56",
    "dsl_table": "201.ppp0",
    "starlink_table": "202.eth1",
    "dsl_mark": "0x1a0000/0x7e0000",
    "starlink_mark": "0x1c0000/0x7e0000",
    "sab_dsl_ip": "172.20.1.243",
    "sab_starlink_ip": "172.20.1.244",
}
PD = "2001:4860:abcd:1200::/56"


def snapshot(mode="dsl", *, delegation=PD):
    config = parse_config(RAW)
    return PolicySnapshot(
        expected_nat(config, mode, delegation),
        ("-A UBIOS_POSTROUTING_USER_HOOK -j IOB_V6_NAT",),
        expected_sab(config),
        ("-A UBIOS_PREROUTING_PBR -j IOB_SAB_PBR",),
        expected_policy(config, mode), (), delegation,
    )


class PolicyTests(unittest.TestCase):
    def test_current_modes_are_recognized_without_a_default_guess(self):
        config = parse_config(RAW)
        self.assertEqual(classify(snapshot("dsl"), config), "dsl")
        self.assertEqual(classify(snapshot("starlink"), config), "starlink")

    def test_missing_hook_extra_rule_or_temp_policy_is_unknown(self):
        config = parse_config(RAW)
        good = snapshot()
        for changed in (
            PolicySnapshot(good.nat_chain, (), good.sab_chain, good.sab_hook,
                           good.policy, good.temporary_policy, good.delegation),
            PolicySnapshot(good.nat_chain + ("-A IOB_V6_NAT -j ACCEPT",), good.nat_hook,
                           good.sab_chain, good.sab_hook, good.policy, (), good.delegation),
            PolicySnapshot(good.nat_chain, good.nat_hook, good.sab_chain, good.sab_hook,
                           good.policy, ("31009: from fd12:3456:789a:bc00::/56 lookup 202.eth1",), good.delegation),
        ):
            self.assertEqual(classify(changed, config), "unknown")
        self.assertTrue(recoverable(PolicySnapshot(
            good.nat_chain, (), good.sab_chain, good.sab_hook,
            good.policy, (), good.delegation), config))
        self.assertFalse(recoverable(PolicySnapshot(
            good.nat_chain + ("-A IOB_V6_NAT -j ACCEPT",), good.nat_hook,
            good.sab_chain, good.sab_hook, good.policy, (), good.delegation), config))

    def test_delegation_rotation_requires_new_verified_rule(self):
        config = parse_config(RAW)
        before = snapshot()
        after = PolicySnapshot(before.nat_chain, before.nat_hook, before.sab_chain,
                               before.sab_hook, before.policy, (), "2001:4860:abcd:3400::/56")
        self.assertEqual(classify(after, config), "unknown")
        self.assertEqual(owned_mode(after, config), ("dsl", PD))
        self.assertNotEqual(before.digest(), after.digest())

    def test_configuration_rejects_broad_or_unmasked_scopes(self):
        for update in ({"ula_prefix": "::/0"}, {"dsl_mark": "0xffffffff/0x7e0000"},
                       {"sab_dsl_ip": "8.8.8.8"}, {"schema": True}):
            with self.subTest(update=update), self.assertRaises(PolicyError):
                parse_config({**RAW, **update})


if __name__ == "__main__":
    unittest.main()
