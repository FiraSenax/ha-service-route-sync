from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.service_route_sync.monitoring_migration import migrate_legacy_checkpoint
from custom_components.service_route_sync.monitoring_store import MonitoringCheckpointError


class MigrationTests(unittest.TestCase):
    def state(self):
        return {
            "engine": {"incidents": {"test": {"active": True, "since": None, "good": None}}},
            "cursor": {"inode": 1, "offset": 10},
            "tracked_port": {"switch": "synthetic", "port": 3},
            "outbox": [{"key": "test", "message": "synthetic", "time": 10, "recovery": False}],
        }

    def files(self):
        return [{"day": "2026-10-12", "inode": 1, "size": 100, "compressed": False}]

    def test_import_preserves_incidents_port_and_pending_notification(self):
        source = self.state()
        result = migrate_legacy_checkpoint(source, self.files())
        self.assertEqual(result["engine"], source["engine"])
        self.assertEqual(result["tracked_port"], source["tracked_port"])
        self.assertEqual(result["outbox"][0]["event"], source["outbox"][0])
        self.assertEqual(result["cursor"], {"day": "2026-10-12", "offset": 10})
        self.assertEqual(result, migrate_legacy_checkpoint(source, self.files()))
        result["engine"]["incidents"].clear()
        self.assertTrue(source["engine"]["incidents"])

    def test_no_guessed_mapping_or_truncation(self):
        for files in (
            [],
            [dict(self.files()[0], compressed=True)],
            [dict(self.files()[0], size=5)],
            self.files() + [dict(self.files()[0], day="2026-10-11")],
        ):
            with self.assertRaises(MonitoringCheckpointError):
                migrate_legacy_checkpoint(self.state(), files)
