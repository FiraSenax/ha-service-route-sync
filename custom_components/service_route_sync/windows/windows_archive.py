"""Bounded daily Wazuh archive replay across in-place gzip compression."""

from dataclasses import dataclass
from datetime import date, timedelta
import gzip
import json
import os
from pathlib import Path
import stat
import zlib

from .windows_cursor import WindowsSourceError

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
DEFAULT_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class DailyCursor:
    day: str
    offset: int

    def __post_init__(self):
        try:
            parsed = date.fromisoformat(self.day)
            if parsed.isoformat() != self.day:
                raise ValueError
        except (TypeError, ValueError):
            raise WindowsSourceError("Invalid Windows archive day") from None
        if type(self.offset) is not int or not 0 <= self.offset <= MAX_ARCHIVE_BYTES:
            raise WindowsSourceError("Invalid Windows archive offset")


@dataclass(frozen=True)
class DailyPage:
    cursor: DailyCursor
    records: tuple
    malformed_records: int
    partial_line: bool
    advanced_day: bool


def _open_daily(root, day):
    """Open only the derived calendar path; never follow directory symlinks."""
    descriptors = []
    try:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(parent)
        for component in (str(day.year), MONTHS[day.month - 1]):
            parent = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(parent)
        filename = f"ossec-alerts-{day.day:02}.json"
        compressed = False
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            fd = os.open(filename + ".gz", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            compressed = True
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise WindowsSourceError("Invalid Windows archive file")
        return os.fdopen(fd, "rb"), compressed
    except OSError:
        raise WindowsSourceError("Windows daily archive unavailable") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read_daily_page(
    root: Path,
    cursor: DailyCursor,
    today: date,
    *,
    maximum_bytes=32768,
    maximum_archive_bytes=DEFAULT_MAX_ARCHIVE_BYTES,
):
    """Offset is uncompressed; gzip replacement never invalidates it.

    The daily archive bound is independent of the bounded response page.
    Compressed files replay their prefix in bounded memory. Missing or corrupt days block
    progress instead of silently skipping data. Persist the returned cursor
    atomically with extracted events, never before accepting the page.
    """
    if (
        not isinstance(cursor, DailyCursor)
        or type(today) is not date
        or type(maximum_bytes) is not int
        or not 1 <= maximum_bytes <= 1048576
        or type(maximum_archive_bytes) is not int
        or not 1 <= maximum_archive_bytes <= MAX_ARCHIVE_BYTES
    ):
        raise WindowsSourceError("Invalid Windows archive request")
    if cursor.offset > maximum_archive_bytes:
        raise WindowsSourceError("Windows archive exceeds configured bound")
    day = date.fromisoformat(cursor.day)
    if day > today:
        raise WindowsSourceError("Windows archive cursor is in the future")
    raw, compressed = _open_daily(root, day)
    with raw:
        stream = gzip.GzipFile(fileobj=raw) if compressed else raw
        try:
            last_byte = None
            if compressed:
                skipped = 0
                while skipped < cursor.offset:
                    chunk = stream.read(min(65536, cursor.offset - skipped))
                    if not chunk:
                        raise WindowsSourceError("Windows archive was truncated")
                    skipped += len(chunk)
                    last_byte = chunk[-1:]
            else:
                if cursor.offset > os.fstat(raw.fileno()).st_size:
                    raise WindowsSourceError("Windows archive was truncated")
                # Live files must not reread their growing prefix every 5 s.
                # gzip offsets are uncompressed, so archives still need replay.
                if cursor.offset:
                    stream.seek(cursor.offset - 1)
                    last_byte = stream.read(1)
                stream.seek(cursor.offset)
            if cursor.offset and last_byte != b"\n":
                raise WindowsSourceError("Windows cursor is not at a record boundary")
            position = cursor.offset
            records, malformed, partial, eof = [], 0, False, False
            while position - cursor.offset < maximum_bytes and len(records) < 1000:
                budget = min(
                    maximum_bytes - (position - cursor.offset), maximum_archive_bytes - position
                )
                if budget <= 0:
                    if not stream.read(1):
                        eof = True
                        break
                    raise WindowsSourceError("Windows archive exceeds configured bound")
                line = stream.readline(budget + 1)
                if not line:
                    eof = True
                    break
                if len(line) > budget:
                    if position == cursor.offset:
                        raise WindowsSourceError("Windows archive record exceeds page bound")
                    break
                if not line.endswith(b"\n"):
                    if day < today or compressed:
                        raise WindowsSourceError("Incomplete historical Windows record")
                    partial = True
                    break
                position += len(line)
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError
                except (ValueError, UnicodeError):
                    malformed += 1
                    continue
                records.append(record)
            advance = eof and day < today
            next_cursor = (
                DailyCursor((day + timedelta(days=1)).isoformat(), 0)
                if advance
                else DailyCursor(cursor.day, position)
            )
            return DailyPage(next_cursor, tuple(records), malformed, partial, advance)
        except (OSError, EOFError, zlib.error):
            raise WindowsSourceError("Corrupt Windows archive") from None
        finally:
            if compressed:
                stream.close()
