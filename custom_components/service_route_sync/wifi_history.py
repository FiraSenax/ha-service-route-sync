"""Durable half-hour WiFi scans and lossless idempotent legacy CSV import."""

import csv
from dataclasses import asdict, fields
import hashlib
import io
import json
import math
import os
import sqlite3
import stat

from .private_sqlite import open_private_database, PrivateDatabaseError
from .wifi_observation import WirelessObservation

COLUMNS = tuple(field.name for field in fields(WirelessObservation))


class WifiHistoryError(ValueError):
    """Sanitized WiFi history failure."""


class WifiHistory:
    def __init__(self, path):
        self.path = path

    def _connect(self):
        try:
            connection = open_private_database(self.path)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                    raise WifiHistoryError("Unexpected unversioned WiFi history")
                connection.executescript(
                    "BEGIN IMMEDIATE;"
                    "CREATE TABLE scans(bucket INTEGER PRIMARY KEY, observed REAL NOT NULL);"
                    "CREATE TABLE observations(bucket INTEGER NOT NULL REFERENCES scans(bucket),mac TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(bucket,mac));"
                    "CREATE TABLE imports(digest TEXT PRIMARY KEY,row_count INTEGER NOT NULL);"
                    "CREATE TABLE legacy(digest TEXT NOT NULL REFERENCES imports(digest),ordinal INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(digest,ordinal));"
                    "PRAGMA user_version=1;COMMIT;"
                )
            elif version != 1:
                raise WifiHistoryError("Unsupported WiFi history version")
            return connection
        except (sqlite3.Error, PrivateDatabaseError):
            if 'connection' in locals():
                connection.close()
            raise WifiHistoryError("WiFi history could not be opened") from None
        except BaseException:
            if 'connection' in locals():
                connection.close()
            raise

    def record(self, observed, clients):
        if type(observed) not in (int, float) or not math.isfinite(observed) or not 0 <= observed <= 2**53 or not isinstance(clients, tuple) or len(clients) > 4096:
            raise WifiHistoryError("Invalid WiFi history scan")
        bucket = int(observed // 1800)
        identities, rows = set(), []
        for client in clients:
            if not isinstance(client, WirelessObservation) or client.mac in identities or client.ts != observed:
                raise WifiHistoryError("Invalid or duplicate WiFi history client")
            identities.add(client.mac)
            try:
                encoded = json.dumps(asdict(client), ensure_ascii=False, allow_nan=False)
            except (ValueError, TypeError, UnicodeError):
                raise WifiHistoryError("Invalid WiFi history payload") from None
            rows.append((bucket, client.mac, encoded))
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT 1 FROM scans WHERE bucket=?", (bucket,)).fetchone():
                    return False
                connection.execute("INSERT INTO scans VALUES(?,?)", (bucket, observed))
                connection.executemany("INSERT INTO observations VALUES(?,?,?)", rows)
            return True
        except sqlite3.Error:
            raise WifiHistoryError("WiFi history scan could not be committed") from None
        finally:
            connection.close()

    def read_page(self, source, after=0, ceiling=None, limit=100):
        """Bounded keyset pagination with a frozen append-only snapshot ceiling.

        Executor-only; callers must authorize access to private client data.
        Rows use the original seventeen-column order for CSV/UI consumers.
        """
        if source not in ('legacy', 'observations') or type(after) is not int or not 0 <= after < 2**63:
            raise WifiHistoryError("Invalid WiFi history page")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise WifiHistoryError("Invalid WiFi history page size")
        if ceiling is not None and (type(ceiling) is not int or not after <= ceiling < 2**63):
            raise WifiHistoryError("Invalid WiFi history snapshot")
        connection = self._connect()
        try:
            # Table names come exclusively from the closed allowlist above.
            if ceiling is None:
                ceiling = connection.execute(f'SELECT coalesce(max(rowid),0) FROM {source}').fetchone()[0]
            if after > ceiling:
                raise WifiHistoryError("Invalid WiFi history snapshot")
            found = connection.execute(
                f'SELECT rowid,payload FROM {source} WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?',
                (after, ceiling, limit + 1)).fetchall()
            rows, size, cursor = [], 0, after
            for identity, payload in found[:limit]:
                payload_size = len(payload.encode('utf-8'))
                if payload_size > 256 * 1024:
                    raise WifiHistoryError("Oversized WiFi history record")
                if rows and size + payload_size > 256 * 1024:
                    break
                value = json.loads(payload)
                if source == 'observations':
                    if not isinstance(value, dict) or set(value) != set(COLUMNS):
                        raise WifiHistoryError("Invalid WiFi history record")
                    value = [value[key] for key in COLUMNS]
                if not isinstance(value, list) or len(value) != len(COLUMNS):
                    raise WifiHistoryError("Invalid WiFi history record")
                rows.append(value)
                size += payload_size
                cursor = identity
            return {'columns': list(COLUMNS), 'rows': rows, 'ceiling': ceiling,
                    'after': cursor, 'more': bool(found and (len(found) > len(rows)))}
        except (sqlite3.Error, ValueError, UnicodeError, TypeError):
            raise WifiHistoryError("WiFi history page could not be read") from None
        finally:
            connection.close()

    def import_csv(self, path, expected_digest):
        # Internal staged-file API: never expose arbitrary paths to a HA service.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        connection = None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024 * 1024:
                raise WifiHistoryError("Invalid WiFi import file")
            with os.fdopen(fd, 'rb') as source:
                fd = None
                digest = hashlib.sha256()
                while chunk := source.read(1048576):
                    digest.update(chunk)
                if digest.hexdigest() != expected_digest:
                    raise WifiHistoryError("WiFi import hash differs from verified source")
                source.seek(0)
                reader = csv.reader(io.TextIOWrapper(source, encoding='utf-8', newline=''), strict=True)
                if tuple(next(reader, ())) != COLUMNS:
                    raise WifiHistoryError("Unexpected WiFi import columns")
                connection = self._connect()
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    existing = connection.execute("SELECT row_count FROM imports WHERE digest=?", (expected_digest,)).fetchone()
                    if existing:
                        return {"imported": False, "rows": existing[0]}
                    connection.execute("INSERT INTO imports VALUES(?,0)", (expected_digest,))
                    count = 0
                    for row in reader:
                        if len(row) != len(COLUMNS) or any(len(value) > 8192 for value in row) or count >= 1000000:
                            raise WifiHistoryError("Invalid WiFi import record")
                        # Keep original CSV cells, including old zeros/empty
                        # values; never retroactively guess missing telemetry.
                        connection.execute("INSERT INTO legacy VALUES(?,?,?)", (expected_digest, count, json.dumps(row, ensure_ascii=False)))
                        count += 1
                    final_info = os.fstat(source.fileno())
                    if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                        final_info.st_size, final_info.st_mtime_ns, final_info.st_ctime_ns
                    ):
                        raise WifiHistoryError("WiFi import source changed during import")
                    connection.execute("UPDATE imports SET row_count=? WHERE digest=?", (count, expected_digest))
                return {"imported": True, "rows": count}
        except (sqlite3.Error, UnicodeError, csv.Error):
            raise WifiHistoryError("WiFi history import could not be committed") from None
        finally:
            if fd is not None:
                os.close(fd)
            if connection is not None:
                connection.close()
