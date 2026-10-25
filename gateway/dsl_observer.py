#!/usr/bin/python3
"""Fixed DSL-only probes, without route changes or caller-selected targets."""

import asyncio
import fcntl
import json
import os
import stat
import time
import re
from configuration import read_private_config

from client_observer import parse_ping
from observer import ObservationError, run

COMMAND = "service-route-sync dsl v1"
TARGETS = {"wan_cf": "1.1.1.1", "wan_google": "8.8.8.8"}


def probe_config():
    raw = read_private_config("/data/service-route-sync/wan-probe.json")
    if (
        set(raw) != {"interface", "mark"}
        or not isinstance(raw["interface"], str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,14}", raw["interface"])
    ):
        raise ValueError("invalid_configuration")
    if (
        raw["interface"] == "lo"
        or type(raw["mark"]) is not int
        or not 0 < raw["mark"] <= 0xFFFFFFFF
    ):
        raise ValueError("invalid_configuration")
    return raw["interface"], str(raw["mark"])


async def route_is_dsl(target, interface, mark):
    code, output, _ = await run(
        ["ip", "-j", "-4", "route", "get", target, "mark", mark, "oif", interface], timeout=2
    )
    if code != 0 or len(output) > 8192:
        return False
    try:
        routes = json.loads(output)
    except ValueError:
        return False
    return (
        isinstance(routes, list)
        and len(routes) == 1
        and isinstance(routes[0], dict)
        and routes[0].get("dev") == interface
        and routes[0].get("type", "unicast") == "unicast"
    )


async def sample(target, interface, mark):
    missing = {"loss": None, "avg": None, "max": None, "rc": 2}
    try:
        if not await route_is_dsl(target, interface, mark):
            return dict(missing, error="dsl_route_unavailable")
        code, output, error = await run(
            [
                "ping",
                "-4",
                "-n",
                "-c",
                "3",
                "-i",
                "0.4",
                "-W",
                "1",
                "-I",
                interface,
                "-m",
                mark,
                target,
            ],
            timeout=5,
        )
        if not await route_is_dsl(target, interface, mark):
            return dict(missing, error="dsl_route_changed")
        if len(output) > 4096 or len(error) > 1024:
            return dict(missing, error="invalid_probe")
        value = parse_ping(code, output.decode("ascii"), error.decode("ascii"))
        if value is None:
            return dict(missing, error="invalid_probe")
        return {
            "loss": value["loss"],
            "avg": value["average_ms"],
            "max": value["maximum_ms"],
            "rc": code,
            "error": None,
        }
    except (OSError, TimeoutError, UnicodeError, ObservationError):
        return dict(missing, error="probe_unavailable")


async def observe():
    interface, mark = probe_config()
    values = await asyncio.gather(*(sample(target, interface, mark) for target in TARGETS.values()))
    return {
        "schema": 1,
        "observed_at_ms": int(time.time() * 1000),
        "probes": dict(zip(TARGETS, values, strict=True)),
    }


def main():
    if os.environ.get("SSH_ORIGINAL_COMMAND") != COMMAND:
        raise ObservationError("command_denied")
    fd = os.open("/run/service-route-sync-dsl.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ObservationError("unsafe_lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = time.monotonic()
        previous = os.read(fd, 128)
        if previous and 0 <= now - float(previous) < 5:
            raise ObservationError("rate_limited")
        os.lseek(fd, 0, 0)
        os.ftruncate(fd, 0)
        os.write(fd, str(now).encode())

        async def bounded():
            async with asyncio.timeout(10):
                return await observe()

        print(json.dumps(asyncio.run(bounded()), separators=(",", ":")))
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('{"schema":1,"error_code":"dsl_observation_failed"}')
        raise SystemExit(1)
