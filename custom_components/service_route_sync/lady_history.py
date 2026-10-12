"""Private durable history journal fenced by the accepted HA checkpoint."""

import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import zlib
import re

from .lady_schema import validate_queues

RETENTION = 14 * 86400


class LadyHistoryError(ValueError):
    """Sanitized journal error; never include event content."""


def revision(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise LadyHistoryError("Invalid history revision")


def clock(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise LadyHistoryError("Invalid history clock")


def journal_path(root, key, epoch=None):
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", key) or key in (".", ".."):
        raise LadyHistoryError("Invalid journal identity")
    if epoch is not None and (not isinstance(epoch, str) or not re.fullmatch(r"[0-9a-f]{64}", epoch)):
        raise LadyHistoryError("Invalid journal epoch")
    return Path(root) / key / (epoch or "observation") / "history.sqlite"


class LadyHistory:
    """Executor-only SQLite operations; no database work on HA's event loop.

    Prepare a revision before committing the HA checkpoint. Readers use the
    checkpoint's history_revision, never the newest SQLite revision. A retry
    removes unaccepted prepares before inserting the replacement batch.
    """

    def __init__(self, path: Path):
        self.path = path

    def _connect(self):
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        parent = self.path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            raise LadyHistoryError("Unsafe history directory")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise LadyHistoryError("Unsafe history database")
        finally:
            os.close(fd)
        try:
            connection = sqlite3.connect(self.path, timeout=2)
        except sqlite3.Error:
            raise LadyHistoryError("History database could not be opened") from None
        try:
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                    raise LadyHistoryError("Unexpected unversioned history database")
                connection.executescript(
                    "BEGIN IMMEDIATE;"
                    "CREATE TABLE samples(revision INTEGER PRIMARY KEY, observed REAL NOT NULL, payload BLOB NOT NULL);"
                    "CREATE TABLE events(revision INTEGER NOT NULL, ordinal INTEGER NOT NULL, observed REAL NOT NULL,"
                    " category TEXT NOT NULL, recovery INTEGER NOT NULL, payload TEXT NOT NULL,"
                    " PRIMARY KEY(revision,ordinal));"
                    "CREATE INDEX event_time ON events(observed);"
                    "CREATE INDEX sample_time ON samples(observed);"
                    "PRAGMA user_version=1;COMMIT;"
                )
            elif version != 1:
                raise LadyHistoryError("Unsupported history database version")
            return connection
        except sqlite3.Error:
            connection.close()
            raise LadyHistoryError("History database could not be initialized") from None
        except BaseException:
            connection.close()
            raise

    def prepare(self, candidate_revision, accepted_revision, observed, sample, events, *, maximum_events=1000):
        revision(candidate_revision)
        revision(accepted_revision)
        clock(observed)
        if type(maximum_events) is not int or not 1 <= maximum_events <= 100000 or candidate_revision <= accepted_revision or not isinstance(sample, dict) or not isinstance(events, (list, tuple)) or len(events) > maximum_events:
            raise LadyHistoryError("Invalid history batch")
        try:
            raw = json.dumps(sample, ensure_ascii=False, allow_nan=False).encode()
            if len(raw) > 131072:
                raise ValueError
            rows = []
            for index, event in enumerate(events):
                encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False)
                identity = hashlib.sha256(encoded.encode()).hexdigest()
                validate_queues([{"delivery_id": identity, "event": event}], [])
                rows.append((candidate_revision, index, event["time"], event["key"], int(event["recovery"]), encoded))
        except (ValueError, TypeError, UnicodeError):
            raise LadyHistoryError("Invalid history payload") from None
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if accepted_revision and connection.execute("SELECT 1 FROM samples WHERE revision=?", (accepted_revision,)).fetchone() is None:
                    raise LadyHistoryError("Accepted history revision is missing")
                connection.execute("DELETE FROM samples WHERE revision>?", (accepted_revision,))
                connection.execute("DELETE FROM events WHERE revision>?", (accepted_revision,))
                connection.execute("INSERT INTO samples VALUES(?,?,?)", (candidate_revision, observed, zlib.compress(raw)))
                connection.executemany("INSERT INTO events VALUES(?,?,?,?,?,?)", rows)
                cutoff = observed - RETENTION
                connection.execute("DELETE FROM events WHERE observed<?", (cutoff,))
                connection.execute("DELETE FROM samples WHERE observed<? AND revision NOT IN(?,?)", (cutoff, candidate_revision, accepted_revision))
        except sqlite3.Error:
            raise LadyHistoryError("History batch could not be prepared") from None
        finally:
            connection.close()

    def prepare_import(self, candidate_revision, observed, events):
        self.prepare(candidate_revision, 0, observed, {"legacy_import": True}, events, maximum_events=100000)

    def snapshot(self, accepted_revision, now):
        revision(accepted_revision)
        clock(now)
        connection = self._connect()
        try:
            with connection:
                # One read transaction prevents counts and recent rows racing.
                connection.execute("BEGIN")
                if accepted_revision and connection.execute("SELECT 1 FROM samples WHERE revision=?", (accepted_revision,)).fetchone() is None:
                    raise LadyHistoryError("Accepted history revision is missing")
                where = "revision<=? AND observed>? AND observed<=?"
                params = (accepted_revision, now - RETENTION, now)
                counts = dict(connection.execute("SELECT category,count(*) FROM events WHERE " + where + " AND recovery=0 GROUP BY category", params))
                rows = connection.execute("SELECT payload FROM events WHERE " + where + " ORDER BY observed DESC,revision DESC,ordinal DESC LIMIT 60", params).fetchall()
                return {"counts": counts, "recent": [json.loads(row[0]) for row in rows]}
        except (sqlite3.Error, ValueError):
            raise LadyHistoryError("History snapshot could not be read") from None
        finally:
            connection.close()
