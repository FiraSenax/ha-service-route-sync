#!/usr/bin/python3
"""Fixed Wazuh reader; install with the standard-library-only windows_reader package."""

import dataclasses
import fcntl
import json
import os
from pathlib import Path
import select
import sys
import time
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent))

from windows_reader.windows_protocol import parse_cursor_request, parse_windows_response
from windows_reader.windows_source import read_windows_batch, read_windows_archive_batch
from windows_reader.windows_archive import DailyCursor


def request(schema):
    content = bytearray()
    deadline = time.monotonic() + 2
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([sys.stdin.buffer], [], [], remaining)[0]:
            raise ValueError("request_timeout")
        chunk = os.read(sys.stdin.fileno(), 1025 - len(content))
        if not chunk:
            return parse_cursor_request(bytes(content), expected_schema=schema)
        content.extend(chunk)
        if len(content) > 1024:
            raise ValueError("request_size")


def bounded_archive_response(root, database, cursor, today):
    """Reduce the archive page until its complete response fits the SSH bound."""
    page_bytes = 1048576
    while True:
        batch = read_windows_archive_batch(
            root,
            database,
            cursor or DailyCursor(today.isoformat(), 0),
            today,
            "013",
            maximum_bytes=page_bytes,
        )
        raw = dataclasses.asdict(batch)
        raw["schema"] = 2
        raw["events"] = list(raw["events"])
        parse_windows_response(raw)
        encoded = json.dumps(raw, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) <= 65536:
            return encoded
        if page_bytes <= 16384:
            raise ValueError("response_size")
        page_bytes //= 2


def main():
    command = os.environ.get("SSH_ORIGINAL_COMMAND")
    if command != "service-route-sync windows v2":
        raise ValueError("command_denied")
    schema = 2
    cursor = request(schema)
    # One small page per invocation; no caller paths, agent or query allowed.
    with open("/run/service-route-sync-windows.lock", "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        database = Path("/var/ossec/queue/db/global.db")
        if schema == 2:
            # Null is bootstrap only. Migration must supply the mapped old day.
            today = date.today()
            encoded = bounded_archive_response(
                Path("/var/ossec/logs/alerts"), database, cursor, today
            )
        else:
            batch = read_windows_batch(
                Path("/var/ossec/logs/alerts/alerts.json"),
                database,
                cursor,
                "013",
                maximum_bytes=32768,
            )
            raw = dataclasses.asdict(batch)
            raw["schema"] = schema
            raw["events"] = list(raw["events"])
            parse_windows_response(raw)
            encoded = json.dumps(raw, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > 65536:
            raise ValueError("response_size")
        sys.stdout.buffer.write(encoded)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Do not expose Windows messages, paths, SSH metadata or SQLite errors.
        sys.stderr.write("windows_observation_failed\n")
        sys.exit(1)
