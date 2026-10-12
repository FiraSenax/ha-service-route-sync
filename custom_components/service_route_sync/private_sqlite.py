"""Open owned private SQLite databases without following file symlinks."""

import os
from pathlib import Path
import sqlite3
import stat


class PrivateDatabaseError(ValueError):
    """Sanitized storage failure."""


def open_private_database(path: Path):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise PrivateDatabaseError("Unsafe private database directory")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise PrivateDatabaseError("Unsafe private database file")
    finally:
        os.close(fd)
    connection = None
    try:
        connection = sqlite3.connect(path, timeout=2)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
    except sqlite3.Error:
        if connection is not None:
            connection.close()
        raise PrivateDatabaseError("Private database could not be opened") from None
