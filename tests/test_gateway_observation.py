"""Fail-closed validation at the root-helper / HA trust boundary."""

from copy import deepcopy
from datetime import datetime, timezone
import unittest

import bootstrap  # noqa: F401
from service_route_sync.gateway_observation import parse_gateway_observation
from service_route_sync.network import NetworkDataError

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
MS = int(NOW.timestamp() * 1000)


def payload():
    return {
        "schema": 1,
        "observed_at_ms": MS,
        "interfaces": [
            {
                "interface": iface,
                "observed_at_ms": MS,
                "link_up": True,
                "ipv4_probe": True,
                "ipv6_probe": True,
                "ipv6_default": True,
                "ipv6_address": True,
                "delegation_known": True,
                "delegated_prefix": None,
            }
            for iface in ("ppp0", "eth1")
        ],
    }


def parse(raw):
    return parse_gateway_observation(raw, NOW, primary_interface="ppp0", secondary_interface="eth1")


class GatewayObservationTests(unittest.TestCase):
    def test_confirmed_carrier_loss_remains_down_when_probe_setup_is_impossible(self):
        raw = payload()
        raw["interfaces"][0].update(
            link_up=False,
            ipv4_probe=None,
            ipv6_probe=None,
            ipv6_default=None,
            ipv6_address=None,
            delegation_known=False,
        )
        sample = parse(raw).primary
        self.assertFalse(sample.ipv4_health().healthy)
        self.assertFalse(sample.ipv6_health(require_delegation=True).healthy)

    def test_successful_probe_conflicting_with_carrier_loss_is_unknown(self):
        raw = payload()
        raw["interfaces"][0]["link_up"] = False
        sample = parse(raw).primary
        self.assertIsNone(sample.ipv4_health().healthy)
        self.assertIsNone(sample.ipv6_health(require_delegation=True).healthy)

    def test_unknown_carrier_does_not_become_down_from_a_failed_probe(self):
        raw = payload()
        raw["interfaces"][0].update(link_up=None, ipv4_probe=False, ipv6_probe=False)
        sample = parse(raw).primary
        self.assertIsNone(sample.ipv4_health().healthy)
        self.assertIsNone(sample.ipv6_health(require_delegation=False).healthy)

    def test_missing_delegation_is_known_absence_not_fake_health(self):
        data = parse(payload())
        self.assertFalse(data.primary.ipv6_health(require_delegation=True).healthy)
        self.assertTrue(data.secondary.ipv6_health(require_delegation=False).healthy)

    def test_unknown_measurement_always_blocks_actuation(self):
        for key in ("link_up", "ipv4_probe", "ipv6_probe", "ipv6_default", "ipv6_address"):
            raw = payload()
            raw["interfaces"][0][key] = None
            value = parse(raw).primary
            if key != "ipv4_probe":
                self.assertIsNone(value.ipv6_health(require_delegation=False).healthy)
        raw = payload()
        raw["interfaces"][0]["delegation_known"] = False
        self.assertIsNone(parse(raw).primary.ipv6_health(require_delegation=True).healthy)

    def test_private_raw_fields_cannot_cross_protocol_boundary(self):
        raw = payload()
        raw["interfaces"][0]["password"] = "private-example"
        with self.assertRaises(NetworkDataError):
            parse(raw)
        raw = payload()
        raw["raw_udapi"] = {}
        with self.assertRaises(NetworkDataError):
            parse(raw)

    def test_nonboolean_truthy_input_is_rejected(self):
        for value in ("true", "false", 1, 0, [], {}):
            raw = payload()
            raw["interfaces"][0]["ipv6_probe"] = value
            with self.assertRaises(NetworkDataError):
                parse(raw)

    def test_stale_future_or_invalid_timestamps_are_rejected(self):
        for value in (MS - 20001, MS + 1, True, "1", 10**30):
            raw = payload()
            raw["observed_at_ms"] = value
            with self.assertRaises(NetworkDataError):
                parse(raw)
        raw = payload()
        raw["interfaces"][0]["observed_at_ms"] = MS - 20001
        with self.assertRaises(NetworkDataError):
            parse(raw)

    def test_duplicate_and_unexpected_interfaces_are_rejected(self):
        for iface in ("eth1", "lo", "ppp0; touch /tmp/unwanted", None, []):
            raw = payload()
            raw["interfaces"][0]["interface"] = iface
            with self.assertRaises(NetworkDataError):
                parse(raw)

    def test_unsafe_prefixes_are_rejected_without_echoing_input(self):
        for prefix in (
            "::/0",
            "fd00::/56",
            "fe80::/56",
            "ff00::/56",
            "2001:4860:1::1/56",
            "2001:4860::/64",
            "invalid",
            42,
        ):
            raw = payload()
            raw["interfaces"][0]["delegated_prefix"] = prefix
            with self.assertRaises(NetworkDataError) as error:
                parse(raw)
            if isinstance(prefix, str):
                self.assertNotIn(prefix, str(error.exception))

    def test_schema_and_knowledge_conflicts_are_rejected(self):
        for version in (True, 2, "1"):
            raw = payload()
            raw["schema"] = version
            with self.assertRaises(NetworkDataError):
                parse(raw)
        raw = payload()
        raw["interfaces"][0].update(delegation_known=False, delegated_prefix="2001:4860::/56")
        with self.assertRaises(NetworkDataError):
            parse(raw)

    def test_supported_public_prefix_and_negative_probe(self):
        raw = payload()
        raw["interfaces"][0]["delegated_prefix"] = "2001:4860::/56"
        before = deepcopy(raw)
        self.assertTrue(parse(raw).primary.ipv6_health(require_delegation=True).healthy)
        self.assertEqual(raw, before)
        raw["interfaces"][0]["ipv6_probe"] = False
        self.assertFalse(parse(raw).primary.ipv6_health(require_delegation=True).healthy)
