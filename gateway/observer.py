#!/usr/bin/python3
"""Read-only UniFi gateway helper for a restricted HA SSH key.

Python 3.9+ and existing OpenSSH/iputils/UDAPI tools. Never invoke a shell or
print raw controller responses: they can contain PPPoE credentials. This
helper cannot modify routing, NAT, files outside its runtime lock, or services.
"""

import asyncio
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import time

COMMAND = "service-route-sync observe v1"
CONFIG_PATH = Path("/data/service-route-sync/observer.json")
LOCK_PATH = Path("/run/service-route-sync-observer.lock")
MAX_OUTPUT = 524288
ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
TARGETS = {
    4: ("1.1.1.1", "8.8.8.8"),
    6: ("2606:4700:4700::1111", "2001:4860:4860::8888"),
}


class ObservationError(Exception):
    """Closed, non-sensitive failure code."""


def load_config(path):
    """Require an owned private file inside an owned private directory."""
    parent = path.parent.lstat()
    info = path.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_mode & 0o077
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & 0o077
    ):
        raise ObservationError("unsafe_configuration")
    with path.open("rb") as source:
        content = source.read(8193)
    if len(content) > 8192:
        raise ObservationError("invalid_configuration")
    return validate_config(json.loads(content))


def validate_config(raw):
    fields = {
        "schema",
        "primary_interface",
        "secondary_interface",
        "primary_table",
        "secondary_table",
        "delegated_prefix_length",
    }
    if (
        not isinstance(raw, dict)
        or set(raw) != fields
        or type(raw["schema"]) is not int
        or raw["schema"] != 1
    ):
        raise ObservationError("invalid_configuration")
    for key in ("primary_interface", "secondary_interface"):
        if not isinstance(raw[key], str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_.-]{0,14}", raw[key]
        ):
            raise ObservationError("invalid_configuration")
        if raw[key] == "lo":
            raise ObservationError("invalid_configuration")
    for key in ("primary_table", "secondary_table"):
        if type(raw[key]) is not int or not 1 <= raw[key] <= 2147483647:
            raise ObservationError("invalid_configuration")
    if (
        raw["primary_interface"] == raw["secondary_interface"]
        or raw["primary_table"] == raw["secondary_table"]
    ):
        raise ObservationError("invalid_configuration")
    if (
        type(raw["delegated_prefix_length"]) is not int
        or not 1 <= raw["delegated_prefix_length"] <= 64
    ):
        raise ObservationError("invalid_configuration")
    return dict(raw)


async def _bounded(stream, maximum):
    result = bytearray()
    while True:
        chunk = await stream.read(min(4096, maximum + 1 - len(result)))
        if not chunk:
            return bytes(result)
        result.extend(chunk)
        if len(result) > maximum:
            raise ObservationError("oversized_response")


async def run(args, timeout=4):
    """Bound local tools without forwarding environment or printing failures."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=ENVIRONMENT,
    )
    tasks = [
        asyncio.create_task(_bounded(process.stdout, MAX_OUTPUT)),
        asyncio.create_task(_bounded(process.stderr, 4096)),
        asyncio.create_task(process.wait()),
    ]
    try:
        stdout, stderr, code = await asyncio.wait_for(asyncio.gather(*tasks), timeout)
        return code, stdout, stderr
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def read_json(args):
    try:
        code, output, _ = await run(args)
        if code != 0:
            return None
        return json.loads(output)
    except (OSError, ValueError, asyncio.TimeoutError, ObservationError):
        return None


def explicit_bool(value):
    return value if isinstance(value, bool) else None


def link_state(row):
    status = row.get("status")
    if not isinstance(status, dict):
        return None
    facts = [explicit_bool(status.get(k)) for k in ("currentEnabled", "plugged")]
    return all(facts) if all(value is not None for value in facts) else None


def delegation(row, prefix_length):
    ipv6 = row.get("ipv6")
    if not isinstance(ipv6, dict) or ipv6.get("dhcp6PDRequestEnabled") is not True:
        return False, None
    values = ipv6.get("dhcp6PDStatus")
    if not isinstance(values, list):
        return False, None
    if not values:
        return True, None
    if len(values) != 1 or not isinstance(values[0], dict):
        return False, None
    try:
        value = values[0].get("network")
        if not isinstance(value, str):
            return False, None
        prefix = ipaddress.IPv6Network(value, strict=True)
        if not prefix.is_global or prefix.is_multicast or prefix.prefixlen != prefix_length:
            return False, None
        return True, str(prefix)
    except ValueError:
        return False, None


def has_global_address(rows, interface):
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    if row.get("ifname") != interface or not isinstance(row.get("addr_info"), list):
        return None
    for address in row["addr_info"]:
        if not isinstance(address, dict):
            return None
        if address.get("family") != "inet6" or address.get("scope") != "global":
            continue
        try:
            value = ipaddress.IPv6Address(address.get("local"))
        except (ValueError, TypeError):
            return None
        if not value.is_global or value.is_multicast:
            continue
        if address.get("tentative") or address.get("dadfailed") or address.get("deprecated"):
            continue
        lifetimes = [address.get(key) for key in ("valid_life_time", "preferred_life_time")]
        if any(
            value != "forever" and (type(value) is not int or not 0 <= value <= 4294967295)
            for value in lifetimes
        ):
            return None
        if 0 in lifetimes:
            continue
        return True
    return False


def has_default(rows, interface):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None
    return any(
        row.get("dst") == "default"
        and row.get("dev") == interface
        and row.get("type") not in ("unreachable", "blackhole", "prohibit", "throw")
        for row in rows
    )


def routing_marks(rows, table):
    """Only existing native marks for the selected table; verify packet routing next."""
    if not isinstance(rows, list):
        return []
    marks = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        named_table = row.get("table")
        if (
            named_table != table
            and named_table != str(table)
            and not (isinstance(named_table, str) and named_table.startswith(str(table) + "."))
        ):
            continue
        try:
            mark = int(str(row.get("fwmark")), 0)
            mask = int(str(row.get("fwmask")), 0)
        except (ValueError, TypeError):
            continue
        if 0 < mark <= 0xFFFFFFFF and 0 < mask <= 0xFFFFFFFF and mark & mask == mark:
            marks.add(mark)
    return sorted(marks)[:3]


async def packet_route(family, target, interface, marks):
    """A local route error is not evidence that a provider failed to answer."""
    for mark in [None] + marks:
        args = ["ip", "-j", "-" + str(family), "route", "get", target, "oif", interface]
        if mark is not None:
            args.extend(["mark", str(mark)])
        rows = await read_json(args)
        if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
            row = rows[0]
            if row.get("dev") == interface and row.get("type") not in (
                "unreachable",
                "blackhole",
                "prohibit",
                "throw",
            ):
                return True, mark
    return False, None


async def probe_target(family, target, interface, marks):
    ready, mark = await packet_route(family, target, interface, marks)
    if not ready:
        return None
    args = ["ping", "-" + str(family), "-n", "-c", "1", "-W", "2", "-I", interface, target]
    if mark is not None:
        args.extend(["-m", str(mark)])  # iputils requires decimal
    try:
        code, _, stderr = await run(args)
    except (OSError, asyncio.TimeoutError, ObservationError):
        return None
    if stderr.strip():
        return None
    if code == 0:
        return True
    if code == 1:
        return False
    return None


async def probes(family, interface, marks):
    results = await asyncio.gather(
        *(probe_target(family, target, interface, marks) for target in TARGETS[family])
    )
    if any(value is True for value in results):
        return True
    return False if all(value is False for value in results) else None


async def observe(config):
    jobs = [read_json(["ubios-udapi-client", "-r", "-t", "3", "GET", "/interfaces"])]
    jobs += [read_json(["ip", "-j", "-" + str(family), "rule", "show"]) for family in (4, 6)]
    for role in ("primary", "secondary"):
        interface = config[role + "_interface"]
        table = str(config[role + "_table"])
        jobs += [
            read_json(["ip", "-j", "-6", "addr", "show", "dev", interface]),
            read_json(["ip", "-j", "-6", "route", "show", "table", table]),
        ]
    data = await asyncio.gather(*jobs)
    interfaces, rules4, rules6 = data[:3]
    if not isinstance(interfaces, list):
        raise ObservationError("gateway_unavailable")
    observations = []
    for index, role in enumerate(("primary", "secondary")):
        interface = config[role + "_interface"]
        table = config[role + "_table"]
        selected = [
            row
            for row in interfaces
            if isinstance(row, dict)
            and isinstance(row.get("identification"), dict)
            and row["identification"].get("id") == interface
        ]
        if len(selected) != 1:
            raise ObservationError("ambiguous_interface")
        row = selected[0]
        status = row.get("status")
        timestamp = status.get("timestamp") if isinstance(status, dict) else None
        if type(timestamp) is not int:
            raise ObservationError("missing_timestamp")
        known, prefix = delegation(row, config["delegated_prefix_length"])
        ipv4, ipv6 = await asyncio.gather(
            probes(4, interface, routing_marks(rules4, table)),
            probes(6, interface, routing_marks(rules6, table)),
        )
        observations.append(
            {
                "interface": interface,
                "observed_at_ms": timestamp,
                "link_up": link_state(row),
                "ipv4_probe": ipv4,
                "ipv6_probe": ipv6,
                "ipv6_default": has_default(data[4 + index * 2], interface),
                "ipv6_address": has_global_address(data[3 + index * 2], interface),
                "delegation_known": known,
                "delegated_prefix": prefix,
            }
        )
    return {"schema": 1, "observed_at_ms": int(time.time() * 1000), "interfaces": observations}


def main():
    if os.environ.get("SSH_ORIGINAL_COMMAND") != COMMAND:
        print(json.dumps({"schema": 1, "error_code": "command_denied"}))
        return 2
    lock = None
    try:
        lock = os.open(str(LOCK_PATH), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ObservationError("busy")
        previous = os.read(lock, 128)
        now = time.monotonic()
        if previous:
            try:
                last = float(previous)
            except ValueError:
                raise ObservationError("invalid_runtime_state")
            if 0 <= now - last < 5:
                raise ObservationError("rate_limited")
        os.lseek(lock, 0, 0)
        os.ftruncate(lock, 0)
        os.write(lock, str(now).encode())
        config = load_config(CONFIG_PATH)

        async def bounded_observation():
            return await asyncio.wait_for(observe(config), 8)

        print(json.dumps(asyncio.run(bounded_observation()), separators=(",", ":")))
        return 0
    except ObservationError as error:
        print(json.dumps({"schema": 1, "error_code": str(error)}))
        return 1
    except Exception:
        print(json.dumps({"schema": 1, "error_code": "observation_failed"}))
        return 1
    finally:
        if lock is not None:
            os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
