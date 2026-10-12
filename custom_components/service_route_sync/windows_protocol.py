"""Closed, bounded protocol for the fixed Windows-agent reader."""

import json
from dataclasses import asdict

from .windows_archive import DailyCursor
from .windows_cursor import AlertCursor, WindowsSourceError
from .windows_queue import normalize_event
from .windows_source import WindowsBatch


def cursor_request(cursor, *, schema=1):
    if type(schema) is not int or schema not in (1, 2):
        raise WindowsSourceError("Invalid Windows protocol version")
    expected = AlertCursor if schema == 1 else DailyCursor
    if cursor is not None and not isinstance(cursor, expected):
        raise WindowsSourceError("Invalid Windows source cursor")
    return json.dumps({"schema": schema, "cursor": None if cursor is None else asdict(cursor)}, separators=(",", ":")).encode()


def parse_cursor_request(raw, *, expected_schema=None):
    if not isinstance(raw, bytes) or len(raw) > 1024:
        raise WindowsSourceError("Invalid Windows request size")
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise WindowsSourceError("Duplicate Windows request field")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError):
        raise WindowsSourceError("Invalid Windows request") from None
    if not isinstance(value, dict) or set(value) != {"schema", "cursor"} or type(value["schema"]) is not int or value["schema"] not in (1, 2):
        raise WindowsSourceError("Invalid Windows request schema")
    if expected_schema is not None and value["schema"] != expected_schema:
        raise WindowsSourceError("Windows command version mismatch")
    return parse_cursor(value["cursor"], nullable=True, schema=value["schema"])


def parse_cursor(raw, *, nullable=False, schema=1):
    if raw is None and nullable:
        return None
    if schema == 2:
        if not isinstance(raw, dict) or set(raw) != {"day", "offset"}:
            raise WindowsSourceError("Invalid Windows daily cursor")
        return DailyCursor(raw["day"], raw["offset"])
    if not isinstance(raw, dict) or set(raw) != {"inode", "offset"}:
        raise WindowsSourceError("Invalid Windows response cursor")
    cursor = AlertCursor(raw["inode"], raw["offset"])
    if cursor.inode > 2**64 - 1 or cursor.offset > 2**63 - 1:
        raise WindowsSourceError("Windows cursor exceeds filesystem bounds")
    return cursor


def parse_windows_response(raw):
    fields = {"schema", "cursor", "events", "agent", "heartbeat_ok", "rotated", "malformed_records", "partial_line"}
    if not isinstance(raw, dict) or set(raw) != fields or type(raw["schema"]) is not int or raw["schema"] not in (1, 2):
        raise WindowsSourceError("Invalid Windows response schema")
    for key in ("heartbeat_ok", "rotated", "partial_line"):
        if type(raw[key]) is not bool:
            raise WindowsSourceError("Invalid Windows response flag")
    if type(raw["malformed_records"]) is not int or not 0 <= raw["malformed_records"] <= 1000:
        raise WindowsSourceError("Invalid Windows malformed count")
    agent = raw["agent"]
    if agent is not None and (not isinstance(agent, dict) or set(agent) != {"last_keepalive"} or type(agent["last_keepalive"]) is not int or agent["last_keepalive"] < 0):
        raise WindowsSourceError("Invalid Windows heartbeat")
    if raw["heartbeat_ok"] != (agent is not None):
        raise WindowsSourceError("Inconsistent Windows heartbeat")
    if not isinstance(raw["events"], list) or len(raw["events"]) > 1000:
        raise WindowsSourceError("Invalid Windows event page")
    events = tuple(normalize_event(event)[0] for event in raw["events"])
    return WindowsBatch(parse_cursor(raw["cursor"], schema=raw["schema"]), events, dict(agent) if agent is not None else None,
                        raw["heartbeat_ok"], raw["rotated"], raw["malformed_records"], raw["partial_line"])
