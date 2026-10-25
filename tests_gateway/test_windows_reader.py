"""The fixed Wazuh SSH reader must page dense logs without dropping events."""

from datetime import date
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


GATEWAY = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY))
spec = importlib.util.spec_from_file_location("windows_observer", GATEWAY / "windows_observer.py")
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)
from windows_reader.windows_archive import DailyCursor  # noqa: E402


class WindowsReaderPagingTests(unittest.TestCase):
    def test_dense_archive_response_is_bounded_and_cursor_has_no_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = date(2026, 10, 12)
            archive = root / "2026" / "Oct" / "ossec-alerts-12.json"
            archive.parent.mkdir(parents=True)
            with archive.open("w") as output:
                for index in range(300):
                    json.dump(
                        {
                            "agent": {"id": "013"},
                            "data": {
                                "win": {
                                    "system": {
                                        "systemTime": "2026-10-12T12:00:00Z",
                                        "providerName": "Test",
                                        "eventID": str(index),
                                        "level": "2",
                                        "message": "x" * 1000,
                                    }
                                }
                            },
                        },
                        output,
                    )
                    output.write("\n")
            cursor = DailyCursor(day.isoformat(), 0)
            seen = []
            pages = 0
            with patch(
                "windows_reader.windows_source.read_heartbeat",
                return_value={"last_keepalive": int(time.time())},
            ):
                while cursor.offset < archive.stat().st_size:
                    response = observer.bounded_archive_response(
                        root, root / "db", cursor, day, "013"
                    )
                    self.assertLessEqual(len(response), 65536)
                    result = json.loads(response)
                    seen.extend(int(event["id"]) for event in result["events"])
                    following = DailyCursor(**result["cursor"])
                    self.assertGreater(following.offset, cursor.offset)
                    cursor = following
                    pages += 1
                    self.assertLess(pages, 20)
            self.assertEqual(seen, list(range(300)))
            self.assertGreater(pages, 1)


if __name__ == "__main__":
    unittest.main()
