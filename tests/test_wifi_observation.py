from datetime import datetime, timezone
from dataclasses import fields
import unittest
import bootstrap  # noqa: F401
from service_route_sync.wifi_observation import observe_wireless, WirelessObservation, WifiDataError

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


class WifiObservationTests(unittest.TestCase):
    def row(self):
        return {
            "mac": "02:00:00:00:00:01",
            "is_wired": False,
            "last_seen": NOW.timestamp(),
            "signal": -53,
            "noise": -94,
            "wifi_tx_retries_percentage": 0,
            "wifi_tx_attempts": 0,
        }

    def test_all_17_legacy_columns_and_valid_zero_signed_values(self):
        self.assertEqual(
            tuple(field.name for field in fields(WirelessObservation)),
            (
                "ts",
                "mac",
                "name",
                "essid",
                "ap_mac",
                "radio",
                "channel",
                "width",
                "signal",
                "noise",
                "tx_rate",
                "rx_rate",
                "nss",
                "retry_pct",
                "tx_retries",
                "wifi_tx_attempts",
                "satisfaction",
            ),
        )
        row = observe_wireless([self.row()], NOW)[0]
        self.assertEqual(row.signal, -53)
        self.assertEqual(row.noise, -94)
        self.assertEqual(row.retry_pct, 0)
        self.assertEqual(row.wifi_tx_attempts, 0)
        self.assertIsNone(row.tx_retries)
        self.assertIsNone(row.ap_mac)

    def test_stale_future_wired_and_unknown_connection_are_not_current_wifi(self):
        for changes in (
            {"last_seen": NOW.timestamp() - 120},
            {"last_seen": NOW.timestamp() + 1},
            {"is_wired": True},
            {"is_wired": None},
        ):
            self.assertEqual(observe_wireless([dict(self.row(), **changes)], NOW), ())

    def test_duplicate_and_malformed_identity_fail_whole_snapshot(self):
        for rows in ([self.row(), self.row()], [dict(self.row(), mac="invalid")], [None]):
            with self.assertRaises(WifiDataError):
                observe_wireless(rows, NOW)

    def test_missing_invalid_and_out_of_range_values_stay_unknown(self):
        raw = dict(
            self.row(),
            signal=True,
            noise=float("nan"),
            wifi_tx_retries_percentage=101,
            tx_rate="100",
            tx_retries=-1,
            channel_width=True,
        )
        row = observe_wireless([raw], NOW)[0]
        for key in ("signal", "noise", "retry_pct", "tx_rate", "tx_retries", "width"):
            self.assertIsNone(getattr(row, key))
