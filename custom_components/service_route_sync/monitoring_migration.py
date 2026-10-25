"""Explicit legacy checkpoint conversion using verified daily-file metadata."""

import copy
from dataclasses import asdict
import hashlib
import json
import math

from .monitoring_store import MonitoringCheckpointError, validate_checkpoint
from .windows_archive import DailyCursor
from .windows_protocol import parse_cursor


def map_legacy_cursor(raw, daily_files):
    cursor = parse_cursor(raw)
    if not isinstance(daily_files, list) or len(daily_files) > 366:
        raise MonitoringCheckpointError("Invalid daily-file inventory")
    matches = set()
    for row in daily_files:
        if not isinstance(row, dict) or set(row) != {"day", "inode", "size", "compressed"}:
            raise MonitoringCheckpointError("Invalid daily-file metadata")
        day = DailyCursor(row["day"], 0).day
        if (
            type(row["inode"]) is not int
            or row["inode"] < 0
            or type(row["size"]) is not int
            or row["size"] < 0
            or type(row["compressed"]) is not bool
        ):
            raise MonitoringCheckpointError("Invalid daily-file metadata values")
        if not row["compressed"] and row["inode"] == cursor.inode:
            if cursor.offset > row["size"]:
                raise MonitoringCheckpointError("Legacy cursor exceeds verified daily file")
            matches.add(day)
    if len(matches) != 1:
        # Compressed archives cannot be matched by their replacement inode.
        # Require independently saved provenance rather than guessing a date.
        raise MonitoringCheckpointError("Legacy cursor day is not uniquely verified")
    return DailyCursor(matches.pop(), cursor.offset)


def migrate_legacy_checkpoint(raw, daily_files):
    if not isinstance(raw, dict) or set(raw) != {"engine", "outbox", "cursor", "tracked_port"}:
        raise MonitoringCheckpointError("Unexpected legacy checkpoint fields")
    if not isinstance(raw["outbox"], list) or len(raw["outbox"]) > 1000:
        raise MonitoringCheckpointError("Invalid legacy outbox")
    candidate = copy.deepcopy(raw)
    candidate["cursor"] = asdict(map_legacy_cursor(raw["cursor"], daily_files))
    candidate["deferred_events"] = []
    candidate["outbox"] = []
    for index, event in enumerate(raw["outbox"]):
        if not isinstance(event, dict) or set(event) != {"key", "message", "time", "recovery"}:
            raise MonitoringCheckpointError("Unsupported legacy notification")
        if (
            not isinstance(event["key"], str)
            or not 1 <= len(event["key"]) <= 200
            or not isinstance(event["message"], str)
            or len(event["message"]) > 8192
            or type(event["recovery"]) is not bool
            or type(event["time"]) not in (int, float)
            or not math.isfinite(event["time"])
        ):
            raise MonitoringCheckpointError("Invalid legacy notification fields")
        identity = hashlib.sha256(
            json.dumps(
                {"legacy": True, "cursor": candidate["cursor"], "index": index, "event": event},
                sort_keys=True,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        candidate["outbox"].append({"delivery_id": identity, "event": copy.deepcopy(event)})
    return validate_checkpoint(candidate)
