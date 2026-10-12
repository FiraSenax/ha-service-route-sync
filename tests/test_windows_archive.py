from datetime import date
import gzip
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import bootstrap  # noqa: F401
from service_route_sync.windows_archive import DailyCursor, read_daily_page
from service_route_sync.windows_cursor import WindowsSourceError


class ArchiveTests(unittest.TestCase):
    def test_large_live_prefix_is_not_reread_on_each_poll(self):
        prefix = b"{}\n" * 500000
        self.path.write_bytes(prefix + b'{"id":2}\n')
        raw = self.path.open("rb")
        spy = MagicMock(wraps=raw)
        spy.__enter__.return_value = spy
        spy.__exit__.side_effect = lambda *args: raw.close()
        with patch("service_route_sync.windows_archive._open_daily", return_value=(spy, False)):
            page = read_daily_page(self.root, DailyCursor("2026-10-11", len(prefix)), self.today)
        self.assertEqual(page.records, ({"id": 2},))
        self.assertEqual(spy.read.call_count, 1)
        self.assertEqual(spy.read.call_args.args, (1,))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "2026" / "Oct"
        self.folder.mkdir(parents=True)
        self.path = self.folder / "ossec-alerts-11.json"
        self.today = date(2026, 10, 12)

    def test_unread_tail_survives_compression_and_day_switch(self):
        content = b'{"id":1}\n{"id":2}\n'
        self.path.write_bytes(content)
        first = read_daily_page(
            self.root, DailyCursor("2026-10-11", 0), self.today, maximum_bytes=9
        )
        self.path.with_suffix(".json.gz").write_bytes(gzip.compress(content))
        self.path.unlink()
        second = read_daily_page(self.root, first.cursor, self.today)
        self.assertEqual(second.records, ({"id": 2},))
        self.assertEqual(second.cursor, DailyCursor("2026-10-12", 0))
        self.assertTrue(second.advanced_day)

    def test_partial_current_line_waits_for_completion(self):
        path = self.folder / "ossec-alerts-12.json"
        path.write_bytes(b'{"id":1')
        cursor = DailyCursor("2026-10-12", 0)
        first = read_daily_page(self.root, cursor, self.today)
        self.assertEqual(first.cursor, cursor)
        self.assertTrue(first.partial_line)
        with path.open("ab") as stream:
            stream.write(b"}\n")
        self.assertEqual(read_daily_page(self.root, cursor, self.today).records, ({"id": 1},))

    def test_missing_truncated_corrupt_archives_do_not_advance(self):
        cursor = DailyCursor("2026-10-11", 10)
        with self.assertRaises(WindowsSourceError):
            read_daily_page(self.root, cursor, self.today)
        self.path.write_bytes(b"{}\n")
        with self.assertRaises(WindowsSourceError):
            read_daily_page(self.root, cursor, self.today)
        self.path.unlink()
        self.path.with_suffix(".json.gz").write_bytes(b"invalid")
        with self.assertRaises(WindowsSourceError):
            read_daily_page(self.root, DailyCursor("2026-10-11", 0), self.today)

    def test_symlinks_and_invalid_dates_are_rejected(self):
        self.path.symlink_to("/etc/passwd")
        with self.assertRaises(WindowsSourceError):
            read_daily_page(self.root, DailyCursor("2026-10-11", 0), self.today)
        for day in ("../etc", "20261011", "2026-02-30"):
            with self.assertRaises(WindowsSourceError):
                DailyCursor(day, 0)

    def test_cursor_inside_a_record_is_rejected_in_plain_and_compressed_files(self):
        content = b'{"id":1}\n'
        self.path.write_bytes(content)
        with self.assertRaises(WindowsSourceError):
            read_daily_page(self.root, DailyCursor("2026-10-11", 4), self.today)
        self.path.with_suffix(".json.gz").write_bytes(gzip.compress(content))
        self.path.unlink()
        with self.assertRaises(WindowsSourceError):
            read_daily_page(self.root, DailyCursor("2026-10-11", 4), self.today)
