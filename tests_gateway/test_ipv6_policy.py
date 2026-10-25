"""Exact managed-rule ownership before any gateway write."""

import unittest

from gateway.ipv6_policy import (
    PolicyError,
    PolicySnapshot,
    classify,
    owned_mode,
    recoverable,
    expected_nat,
    expected_policy,
    expected_sab,
    parse_config,
)


RAW = {
    "schema": 1,
    "ula_prefix": "fd12:3456:789a:bc00::/56",
    "primary_interface": "ppp0",
    "secondary_interface": "eth1",
    "primary_table": "201.ppp0",
    "secondary_table": "202.eth1",
    "primary_mark": "0x1a0000/0x7e0000",
    "secondary_mark": "0x1c0000/0x7e0000",
    "sab_primary_ip": "172.20.1.243",
    "sab_secondary_ip": "172.20.1.244",
}
PD = "2001:4860:abcd:1200::/56"


def snapshot(mode="primary", *, delegation=PD):
    config = parse_config(RAW)
    return PolicySnapshot(
        expected_nat(config, mode, delegation),
        ("-A UBIOS_POSTROUTING_USER_HOOK -j IOB_V6_NAT",),
        expected_sab(config),
        ("-A UBIOS_PREROUTING_PBR -j IOB_SAB_PBR",),
        expected_policy(config, mode),
        (),
        delegation,
    )


class PolicyTests(unittest.TestCase):
    def test_other_installations_can_use_matching_prefix_lengths_and_interface_names(self):
        for length in [48, 56, 60, 64]:
            config = parse_config(
                {
                    **RAW,
                    "ula_prefix": f"fd34:5678:9abc::/{length}",
                    "primary_interface": "wan.100",
                    "secondary_interface": "uplink2",
                    "primary_table": "120",
                    "secondary_table": "130",
                }
            )
            delegation = f"2001:4860:abcd::/{length}"
            self.assertIn(f"--to {delegation}", expected_nat(config, "primary", delegation)[1])
            self.assertIn("-o uplink2", expected_nat(config, "secondary", None)[1])
            mismatch = 64 if length != 64 else 56
            with self.assertRaises(PolicyError):
                expected_nat(config, "primary", f"2001:4860:abcd::/{mismatch}")
        for prefix in ["2001:db8::/48", "fe80::/64", "fd00::/32", "fd00::/80"]:
            with self.assertRaises(PolicyError):
                parse_config({**RAW, "ula_prefix": prefix})

    def test_current_unifi_marks_preserve_the_configured_mask(self):
        config = parse_config(
            {
                **RAW,
                "primary_mark": "0x21940000/0x3ffe0000",
                "secondary_mark": "0x21960000/0x3ffe0000",
            }
        )
        rules = expected_sab(config)
        self.assertIn("--set-xmark 0x21960000/0x3ffe0000", rules[1])
        self.assertIn("--nfmask 0x3ffe0000 --ctmask 0x3ffe0000", rules[2])
        for changes in (
            {"primary_mark": "0x0/0x3ffe0000"},
            {"primary_mark": "0x21940001/0x3ffe0000"},
            {"primary_mark": "0x21940000/0x3ffe0000"},
            {"secondary_mark": "0x01a0000/0x7e0000"},
        ):
            with self.subTest(changes=changes), self.assertRaises(PolicyError):
                parse_config({**RAW, **changes})

    def test_current_modes_are_recognized_without_a_default_guess(self):
        config = parse_config(RAW)
        self.assertEqual(classify(snapshot("primary"), config), "primary")
        self.assertEqual(classify(snapshot("secondary"), config), "secondary")

    def test_missing_hook_extra_rule_or_temp_policy_is_unknown(self):
        config = parse_config(RAW)
        good = snapshot()
        for changed in (
            PolicySnapshot(
                good.nat_chain,
                (),
                good.sab_chain,
                good.sab_hook,
                good.policy,
                good.temporary_policy,
                good.delegation,
            ),
            PolicySnapshot(
                good.nat_chain + ("-A IOB_V6_NAT -j ACCEPT",),
                good.nat_hook,
                good.sab_chain,
                good.sab_hook,
                good.policy,
                (),
                good.delegation,
            ),
            PolicySnapshot(
                good.nat_chain,
                good.nat_hook,
                good.sab_chain,
                good.sab_hook,
                good.policy,
                ("31009: from fd12:3456:789a:bc00::/56 lookup 202.eth1",),
                good.delegation,
            ),
        ):
            self.assertEqual(classify(changed, config), "unknown")
        self.assertTrue(
            recoverable(
                PolicySnapshot(
                    good.nat_chain,
                    (),
                    good.sab_chain,
                    good.sab_hook,
                    good.policy,
                    (),
                    good.delegation,
                ),
                config,
            )
        )
        self.assertFalse(
            recoverable(
                PolicySnapshot(
                    good.nat_chain + ("-A IOB_V6_NAT -j ACCEPT",),
                    good.nat_hook,
                    good.sab_chain,
                    good.sab_hook,
                    good.policy,
                    (),
                    good.delegation,
                ),
                config,
            )
        )

    def test_delegation_rotation_requires_new_verified_rule(self):
        config = parse_config(RAW)
        before = snapshot()
        after = PolicySnapshot(
            before.nat_chain,
            before.nat_hook,
            before.sab_chain,
            before.sab_hook,
            before.policy,
            (),
            "2001:4860:abcd:3400::/56",
        )
        self.assertEqual(classify(after, config), "unknown")
        self.assertEqual(owned_mode(after, config), ("primary", PD))
        self.assertNotEqual(before.digest(), after.digest())

    def test_configuration_rejects_broad_or_unmasked_scopes(self):
        for update in (
            {"ula_prefix": "::/0"},
            {"primary_mark": "0xffffffff/0x7e0000"},
            {"sab_primary_ip": "8.8.8.8"},
            {"schema": True},
        ):
            with self.subTest(update=update), self.assertRaises(PolicyError):
                parse_config({**RAW, **update})


if __name__ == "__main__":
    unittest.main()


class ConfigurablePolicyTests(unittest.TestCase):
    def test_non_personal_interfaces_and_tables_preserve_ownership_checks(self):
        config = parse_config(
            {
                **RAW,
                "primary_interface": "wan0",
                "secondary_interface": "wan1",
                "primary_table": "301.fiber",
                "secondary_table": "302.mobile",
            }
        )
        self.assertIn("-o wan0", expected_nat(config, "primary", PD)[1])
        self.assertIn("-o wan1", expected_nat(config, "secondary", None)[1])
        self.assertIn("lookup 301.fiber", expected_policy(config, "primary")[0])
        observed = PolicySnapshot(
            expected_nat(config, "primary", PD),
            ("-A UBIOS_POSTROUTING_USER_HOOK -j IOB_V6_NAT",),
            expected_sab(config),
            ("-A UBIOS_PREROUTING_PBR -j IOB_SAB_PBR",),
            expected_policy(config, "primary"),
            (),
            PD,
        )
        self.assertEqual(classify(observed, config), "primary")
        self.assertEqual(classify(observed, parse_config(RAW)), "unknown")

    def test_invalid_interface_or_table_never_becomes_a_command_argument(self):
        for key, value in (
            ("primary_interface", "wan0; reboot"),
            ("secondary_table", "--help"),
            ("primary_interface", "lo"),
            ("secondary_interface", RAW["primary_interface"]),
        ):
            with self.assertRaises(PolicyError):
                parse_config({**RAW, key: value})
