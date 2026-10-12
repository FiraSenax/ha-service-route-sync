"""Checkpointable Windows replay window; callers commit before publishing."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math

from .windows_cursor import WindowsSourceError


def normalize_event(event):
    fields = {"time", "provider", "id", "level", "message"}
    if not isinstance(event, dict) or set(event) != fields:
        raise WindowsSourceError("Invalid queued Windows event")
    for key, limit in (("time", 64), ("provider", 200), ("message", 8192)):
        if not isinstance(event[key], str) or len(event[key]) > limit:
            raise WindowsSourceError("Invalid queued Windows field")
    if not event["provider"]:
        raise WindowsSourceError("Missing Windows provider")
    identifier = event["id"]
    if not isinstance(identifier, str) or not identifier.isascii() or not identifier.isdigit() or not 1 <= len(identifier) <= 5 or int(identifier) > 65535:
        raise WindowsSourceError("Invalid Windows event identifier")
    if event["level"] not in (None, "0", "1", "2", "3", "4", "5"):
        raise WindowsSourceError("Invalid Windows event level")
    try:
        stamp = datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
        observed = stamp.timestamp()
        canonical = dict(event, time=stamp.astimezone(timezone.utc).isoformat(), id=str(int(identifier)))
        encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")
    except (ValueError, OverflowError, UnicodeError):
        raise WindowsSourceError("Invalid Windows event encoding or time") from None
    return canonical, observed, hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ReplayWindow:
    checkpoint: list
    due: tuple
    expired: int


def prepare_replay(checkpoint, incoming, now):
    """Retain delivered identities for 5 min and future events for <=5 min.

    Overflow fails the whole candidate, so the caller must not advance its
    source cursor. The returned checkpoint belongs in the same atomic save as
    incident state, cursor and delivery outbox; this function performs no I/O.
    """
    if type(now) not in (int, float) or not math.isfinite(now):
        raise WindowsSourceError("Invalid replay clock")
    if not isinstance(checkpoint, list) or len(checkpoint) > 1000 or not isinstance(incoming, (list, tuple)) or len(incoming) > 1000:
        raise WindowsSourceError("Invalid replay queue size")
    rows = {}
    for row in checkpoint:
        if not isinstance(row, dict) or set(row) != {"event", "delivered"} or type(row["delivered"]) is not bool:
            raise WindowsSourceError("Invalid replay checkpoint")
        event, observed, identity = normalize_event(row["event"])
        if identity in rows:
            raise WindowsSourceError("Duplicate replay checkpoint identity")
        rows[identity] = (event, observed, row["delivered"])
    for raw in incoming:
        event, observed, identity = normalize_event(raw)
        rows.setdefault(identity, (event, observed, False))
    retained, due, expired = [], [], 0
    for event, observed, delivered in sorted(rows.values(), key=lambda row: row[1]):
        if observed < now - 300:
            expired += 1
            continue
        if observed > now + 300:
            # Do not silently consume a source page with excessive clock skew.
            raise WindowsSourceError("Windows clock skew exceeds replay window")
        if observed <= now and not delivered:
            due.append(dict(event))
            delivered = True
        retained.append({"event": dict(event), "delivered": delivered})
    if len(retained) > 1000:
        raise WindowsSourceError("Windows replay queue is full")
    return ReplayWindow(retained, tuple(due), expired)
