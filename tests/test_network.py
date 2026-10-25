"""WAN payloads must preserve unknown health and physical units."""

import unittest
from datetime import datetime, timedelta, timezone

import bootstrap  # noqa: F401
from service_route_sync.network.network import NetworkDataError, parse_gateway, parse_wan

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


class NetworkTests(unittest.TestCase):
    def test_missing_fields_remain_unknown(self):
        sample = parse_wan("wan1", {}, NOW)
        self.assertIsNone(sample.link_up)
        self.assertIsNone(sample.availability_pct)
        self.assertIsNone(sample.latency_ms)
        self.assertIsNone(sample.download_mbit_s)

    def test_window_availability_does_not_override_current_link(self):
        sample = parse_wan("wan1", {"up": False, "availability": 95}, NOW)
        self.assertFalse(sample.link_up)
        self.assertEqual(sample.availability_pct, 95)
        self.assertIsNone(parse_wan("wan1", {"availability": 100}, NOW).link_up)

    def test_rates_are_bytes_per_second_not_bits(self):
        sample = parse_wan("wan1", {"rx_bytes-r": 1_250_000, "tx_bytes-r": 0}, NOW)
        self.assertEqual(sample.download_mbit_s, 10)
        self.assertEqual(sample.upload_mbit_s, 0)

    def test_bad_numbers_never_become_zero_or_online(self):
        for value in (True, "100", float("inf"), float("nan"), -1, 101):
            with self.subTest(value=value):
                self.assertIsNone(parse_wan("wan1", {"availability": value}, NOW).availability_pct)

    def test_partial_gateway_is_supported_but_blank_is_not(self):
        sample = parse_gateway({"wan1": {"up": True}}, NOW)
        self.assertTrue(sample.primary.link_up)
        self.assertIsNone(sample.secondary)
        for raw in ({}, [], {"wan1": []}):
            with self.assertRaises(NetworkDataError):
                parse_gateway(raw, NOW)

    def test_future_and_stale_samples_are_rejected(self):
        sample = parse_wan("wan1", {}, NOW)
        self.assertFalse(sample.is_fresh(NOW - timedelta(seconds=1), timedelta(seconds=20)))
        self.assertFalse(sample.is_fresh(NOW + timedelta(seconds=21), timedelta(seconds=20)))
        self.assertTrue(sample.is_fresh(NOW + timedelta(seconds=20), timedelta(seconds=20)))

    def test_huge_numbers_are_unknown_without_overflow(self):
        for value in (10**1000, 1e308, 1_000_000_000_001):
            sample = parse_wan("wan1", {"rx_bytes-r": value, "tx_bytes-r": value}, NOW)
            self.assertIsNone(sample.download_mbit_s)
            self.assertIsNone(sample.upload_mbit_s)
