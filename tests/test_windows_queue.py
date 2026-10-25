import unittest
import bootstrap  # noqa: F401
from service_route_sync.windows.windows_queue import prepare_replay
from service_route_sync.windows.windows_cursor import WindowsSourceError


class ReplayTests(unittest.TestCase):
    def event(self, stamp="2026-10-12T00:01:00Z", message="test"):
        return {"time": stamp, "provider": "User32", "id": "1074", "level": "4", "message": message}

    def test_future_event_survives_checkpoint_then_delivers_once(self):
        now = 1791763200
        first = prepare_replay([], [self.event()], now)
        self.assertEqual(first.due, ())
        second = prepare_replay(first.checkpoint, [self.event()], now + 60)
        self.assertEqual(len(second.due), 1)
        third = prepare_replay(second.checkpoint, [self.event()], now + 61)
        self.assertEqual(third.due, ())
        self.assertFalse(first.checkpoint[0]["delivered"])

    def test_timezone_and_identifier_aliases_deduplicate(self):
        alias = self.event("2026-10-12T02:01:00+02:00")
        alias["id"] = "01074"
        result = prepare_replay([], [self.event(), alias], 1791763260)
        self.assertEqual(len(result.due), 1)

    def test_expired_events_are_counted(self):
        result = prepare_replay([], [self.event()], 1791763561)
        self.assertEqual(result.expired, 1)
        self.assertEqual(result.checkpoint, [])

    def test_backpressure_and_invalid_input_leave_original_unchanged(self):
        original = prepare_replay([], [self.event()], 1791763200).checkpoint
        for incoming in ([self.event("2026-10-12T01:00:00Z")], [dict(self.event(), id=True)]):
            with self.assertRaises(WindowsSourceError):
                prepare_replay(original, incoming, 1791763200)
        self.assertFalse(original[0]["delivered"])
        events = [self.event(message=str(index)) for index in range(1000)]
        full = prepare_replay([], events, 1791763200)
        with self.assertRaises(WindowsSourceError):
            prepare_replay(full.checkpoint, [self.event(message="extra")], 1791763200)
