from pathlib import Path
import tempfile
import unittest
import bootstrap  # noqa: F401
from service_route_sync.windows_cursor import AlertCursor, read_alert_page, WindowsSourceError


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "alerts.json"

    def test_partial_line_is_not_checkpointed_and_is_read_after_completion(self):
        self.path.write_bytes(b'{"id":1}\n{"id":2')
        first = read_alert_page(self.path, None)
        self.assertEqual(first.cursor.offset, 9)
        self.assertTrue(first.partial_line)
        with self.path.open("ab") as f:
            f.write(b"}\n")
        second = read_alert_page(self.path, first.cursor)
        self.assertEqual(second.records, ({"id": 2},))

    def test_malformed_record_does_not_block_following_valid_record(self):
        self.path.write_bytes(b'invalid\nnull\n{"id":3}\n')
        page = read_alert_page(self.path, None)
        self.assertEqual(page.malformed_records, 2)
        self.assertEqual(page.records, ({"id": 3},))

    def test_rotation_and_truncation_reset_with_explicit_flag(self):
        self.path.write_bytes(b'{"id":1}\n')
        first = read_alert_page(self.path, None)
        self.path.rename(self.path.with_suffix(".old"))
        self.path.write_bytes(b'{"id":2}\n')
        second = read_alert_page(self.path, first.cursor)
        self.assertTrue(second.rotated)
        self.assertEqual(second.records, ({"id": 2},))
        self.path.write_bytes(b"")
        self.assertTrue(read_alert_page(self.path, second.cursor).rotated)

    def test_page_limit_returns_complete_record_cursor(self):
        self.path.write_bytes(b'{"id":1}\n{"id":2}\n')
        page = read_alert_page(self.path, None, maximum_bytes=12)
        self.assertEqual(page.cursor.offset, 9)
        self.assertEqual(page.records, ({"id": 1},))

    def test_strict_reader_never_skips_unread_rotated_tail(self):
        self.path.write_bytes(b'{"id":1}\n{"id":2}\n')
        first = read_alert_page(self.path, None, maximum_bytes=9)
        self.path.rename(self.path.with_suffix(".old"))
        self.path.write_bytes(b'{"id":3}\n')
        with self.assertRaises(WindowsSourceError):
            read_alert_page(self.path, first.cursor, require_continuity=True)
        self.assertEqual(first.cursor.offset, 9)

    def test_strict_reader_rejects_truncation_and_invalid_policy(self):
        self.path.write_bytes(b'{"id":1}\n')
        first = read_alert_page(self.path, None)
        self.path.write_bytes(b"")
        with self.assertRaises(WindowsSourceError):
            read_alert_page(self.path, first.cursor, require_continuity=True)
        with self.assertRaises(WindowsSourceError):
            read_alert_page(self.path, None, require_continuity=1)

    def test_cursor_and_symlink_boundaries(self):
        with self.assertRaises(WindowsSourceError):
            AlertCursor(True, 0)
        target = Path(self.directory.name) / "target"
        target.write_text("{}\n")
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            read_alert_page(self.path, None)


if __name__ == "__main__":
    unittest.main()
