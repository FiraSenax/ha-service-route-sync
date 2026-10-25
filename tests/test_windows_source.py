from pathlib import Path
import sqlite3
import tempfile
import unittest
import bootstrap  # noqa: F401
from service_route_sync.windows.windows_source import (
    extract_events,
    read_heartbeat,
    read_windows_batch,
)
from service_route_sync.windows.windows_cursor import WindowsSourceError


class WindowsSourceTests(unittest.TestCase):
    def event(self, agent="013"):
        return {
            "agent": {"id": agent},
            "data": {
                "win": {
                    "system": {
                        "systemTime": "2026-10-12T01:00:00Z",
                        "providerName": "User32",
                        "eventID": 1074,
                        "level": 4,
                        "message": "test",
                    }
                }
            },
        }

    def test_only_configured_agent_and_reduced_fields(self):
        rows = extract_events(
            [self.event(), self.event("014"), {"agent": {"id": "013"}, "data": None}], "013"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "1074")
        self.assertEqual(rows[0]["level"], "4")
        self.assertEqual(set(rows[0]), {"time", "provider", "id", "level", "message"})

    def test_invalid_agent_and_event_ids_rejected(self):
        with self.assertRaises(WindowsSourceError):
            extract_events([], "13 OR 1=1")
        row = self.event()
        row["data"]["win"]["system"]["eventID"] = True
        self.assertEqual(extract_events([row], "013"), ())

    def test_heartbeat_read_only_and_missing_database_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "global.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE agent(id INTEGER PRIMARY KEY,last_keepalive INTEGER)")
                db.execute("INSERT INTO agent VALUES(13,1234)")
            self.assertEqual(read_heartbeat(path, "013"), {"last_keepalive": 1234})
            missing = Path(directory) / "missing.db"
            with self.assertRaises(sqlite3.Error):
                read_heartbeat(missing, "013")
            self.assertFalse(missing.exists())

    def test_heartbeat_failure_does_not_discard_alert_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alerts.json"
            path.write_text("{}\n")
            batch = read_windows_batch(path, Path(directory) / "missing.db", None, "013")
            self.assertFalse(batch.heartbeat_ok)
            self.assertEqual(batch.cursor.offset, 3)


if __name__ == "__main__":
    unittest.main()
