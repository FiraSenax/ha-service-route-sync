import unittest
import bootstrap  # noqa: F401
from service_route_sync.windows.windows_cursor import AlertCursor, WindowsSourceError
from service_route_sync.windows.windows_archive import DailyCursor
from service_route_sync.windows.windows_protocol import (
    cursor_request,
    parse_cursor_request,
    parse_windows_response,
)


class ProtocolTests(unittest.TestCase):
    def test_daily_protocol_is_versioned_and_strict(self):
        cursor = DailyCursor("2026-10-11", 10)
        frame = cursor_request(cursor, schema=2)
        self.assertEqual(parse_cursor_request(frame, expected_schema=2), cursor)
        with self.assertRaises(WindowsSourceError):
            parse_cursor_request(frame, expected_schema=1)
        response = dict(self.response(), schema=2, cursor={"day": cursor.day, "offset": 10})
        self.assertEqual(parse_windows_response(response).cursor, cursor)
        with self.assertRaises(WindowsSourceError):
            cursor_request(AlertCursor(1, 10), schema=2)

    def response(self):
        return {
            "schema": 1,
            "cursor": {"inode": 1, "offset": 10},
            "events": [],
            "agent": {"last_keepalive": 10},
            "heartbeat_ok": True,
            "rotated": False,
            "malformed_records": 0,
            "partial_line": False,
        }

    def test_request_only_accepts_cursor(self):
        self.assertIsNone(parse_cursor_request(cursor_request(None)))
        self.assertEqual(
            parse_cursor_request(cursor_request(AlertCursor(1, 10))), AlertCursor(1, 10)
        )
        for raw in (
            b'{"schema":1,"schema":1,"cursor":null}',
            b'{"schema":1,"cursor":null,"agent":"014"}',
            b'{"schema":true,"cursor":null}',
        ):
            with self.assertRaises(WindowsSourceError):
                parse_cursor_request(raw)

    def test_closed_response_and_strict_flags(self):
        self.assertEqual(parse_windows_response(self.response()).cursor.offset, 10)
        for key, value in (
            ("heartbeat_ok", 1),
            ("agent", None),
            ("cursor", {"inode": True, "offset": 10}),
            ("malformed_records", -1),
        ):
            with self.assertRaises(WindowsSourceError):
                parse_windows_response(dict(self.response(), **{key: value}))

    def test_no_foreign_fields_or_unvalidated_events(self):
        with self.assertRaises(WindowsSourceError):
            parse_windows_response(dict(self.response(), command="id"))
        with self.assertRaises(WindowsSourceError):
            parse_windows_response(dict(self.response(), events=[{"message": "invalid"}]))
