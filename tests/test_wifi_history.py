import csv
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import bootstrap  # noqa: F401
from service_route_sync.wifi_history import WifiHistory, WifiHistoryError, COLUMNS
from service_route_sync.wifi_observation import observe_wireless


class WifiHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "private" / "wifi.sqlite"
        self.history = WifiHistory(self.path)

    def clients(self, ts):
        return observe_wireless(
            [{"mac": "02:00:00:00:00:01", "is_wired": False, "last_seen": ts, "signal": -50}],
            datetime.fromtimestamp(ts, timezone.utc),
        )

    def csv_file(self, bad=False):
        path = self.root / "legacy.csv"
        with path.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(COLUMNS)
            writer.writerow(["1000", "02:00:00:00:00:01", "name, with comma", "ssid"] + ["0"] * 13)
            if bad:
                writer.writerow(["truncated"])
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_half_hour_scan_is_durable_and_restart_does_not_duplicate_it(self):
        self.assertTrue(self.history.record(1000, self.clients(1000)))
        self.assertFalse(WifiHistory(self.path).record(1001, self.clients(1001)))
        self.assertTrue(self.history.record(1800, ()))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM scans").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM observations").fetchone()[0], 1)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_csv_import_is_lossless_and_idempotent(self):
        path, digest = self.csv_file()
        self.assertEqual(self.history.import_csv(path, digest), {"imported": True, "rows": 1})
        self.assertEqual(
            WifiHistory(self.path).import_csv(path, digest), {"imported": False, "rows": 1}
        )
        with sqlite3.connect(self.path) as db:
            self.assertIn(
                "name, with comma", db.execute("SELECT payload FROM legacy").fetchone()[0]
            )

    def test_pagination_freezes_snapshot_and_preserves_native_nulls(self):
        self.history.record(1000, self.clients(1000))
        self.history.record(1800, self.clients(1800))
        first = self.history.read_page("observations", limit=1)
        self.assertTrue(first["more"])
        self.history.record(3600, self.clients(3600))
        second = self.history.read_page("observations", first["after"], first["ceiling"], limit=1)
        self.assertFalse(second["more"])
        self.assertEqual(first["rows"][0][0], 1000)
        self.assertEqual(second["rows"][0][0], 1800)
        self.assertIsNone(first["rows"][0][3])
        self.assertEqual(len(self.history.read_page("observations")["rows"]), 3)

    def test_legacy_page_preserves_cells_and_rejects_unbounded_or_sql_inputs(self):
        path, digest = self.csv_file()
        self.history.import_csv(path, digest)
        page = self.history.read_page("legacy")
        self.assertEqual(page["rows"][0][2], "name, with comma")
        self.assertEqual(page["rows"][0][-1], "0")
        self.assertFalse(page["more"])
        for arguments in [
            ("legacy; DROP TABLE scans",),
            ("legacy", True),
            ("legacy", 0, None, 101),
            ("legacy", 1, 0),
        ]:
            with self.assertRaises(WifiHistoryError):
                self.history.read_page(*arguments)

    def test_bad_csv_rolls_back_entire_import_and_wrong_hash_is_rejected(self):
        path, digest = self.csv_file(bad=True)
        with self.assertRaises(WifiHistoryError):
            self.history.import_csv(path, digest)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM imports").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM legacy").fetchone()[0], 0)
        with self.assertRaises(WifiHistoryError):
            self.history.import_csv(path, "0" * 64)

    def test_source_change_after_hash_validation_rolls_back_import(self):
        path, digest = self.csv_file()
        original_connect = self.history._connect

        def change_source():
            # Modify the verified inode while preserving valid CSV syntax.
            with path.open("a", newline="") as stream:
                csv.writer(stream).writerow(["1001"] + ["changed"] * 16)
            return original_connect()

        with patch.object(self.history, "_connect", side_effect=change_source):
            with self.assertRaisesRegex(WifiHistoryError, "source changed"):
                self.history.import_csv(path, digest)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM imports").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM legacy").fetchone()[0], 0)
