from pathlib import Path
import tempfile
import unittest
import bootstrap  # noqa: F401
from service_route_sync.monitoring_history import (
    MonitoringHistory,
    MonitoringHistoryError,
    RETENTION,
)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "history.sqlite"
        self.history = MonitoringHistory(self.path)

    def event(self, now, key="synthetic", recovery=False):
        return {"time": now, "key": key, "message": "synthetic", "recovery": recovery}

    def test_failed_checkpoint_prepare_is_invisible_and_retry_removes_phantoms(self):
        self.history.prepare(1, 0, 1000, {}, [self.event(1000)])
        self.assertEqual(self.history.snapshot(0, 1000)["counts"], {})
        self.history.prepare(2, 0, 1001, {}, [self.event(1001, "retry")])
        snapshot = MonitoringHistory(self.path).snapshot(2, 1001)
        self.assertEqual(snapshot["counts"], {"retry": 1})

    def test_restart_retention_counts_recovery_and_recent_bound(self):
        events = [self.event(1000 + i) for i in range(70)] + [self.event(1070, recovery=True)]
        self.history.prepare(1, 0, 1070, {}, events)
        snapshot = MonitoringHistory(self.path).snapshot(1, 1070)
        self.assertEqual(snapshot["counts"], {"synthetic": 70})
        self.assertEqual(len(snapshot["recent"]), 60)
        self.assertTrue(snapshot["recent"][0]["recovery"])
        self.assertEqual(self.history.snapshot(1, 1070 + RETENTION)["counts"], {})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_missing_accepted_revision_does_not_look_like_empty_healthy_history(self):
        with self.assertRaises(MonitoringHistoryError):
            self.history.snapshot(1, 1000)

    def test_failed_new_prepare_cannot_remove_the_last_accepted_marker(self):
        self.history.prepare(1, 0, 1000, {}, [self.event(1000)])
        self.history.prepare(2, 1, 1000 + RETENTION + 1, {}, [])
        self.assertEqual(self.history.snapshot(1, 1000 + RETENTION + 1)["counts"], {})

    def test_invalid_payload_and_symlink_do_not_mutate_target(self):
        for event in (self.event(True), dict(self.event(1000), recovery=1)):
            with self.assertRaises(MonitoringHistoryError):
                self.history.prepare(1, 0, 1000, {}, [event])
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        target = self.path.parent / "target"
        target.write_text("unchanged")
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            self.history.prepare(1, 0, 1000, {}, [])
        self.assertEqual(target.read_text(), "unchanged")
