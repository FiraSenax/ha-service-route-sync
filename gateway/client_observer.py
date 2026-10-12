#!/usr/bin/python3
"""Gateway-owned MAC scope; validated active IP selections and fixed read commands."""

import asyncio
import fcntl
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import stat
import select
import sys
import time

from observer import ObservationError, run

ROUTES = {0x21940000: "dsl", 0x21960000: "starlink", 0x21660000: "vpn2", 0x20040000: "vpn1"}
PRIVATE_NETWORKS = tuple(
    ipaddress.IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
COMMAND = "service-route-sync clients v1"
CONFIG_PATH = Path("/data/service-route-sync/clients.json")
LOCK_PATH = Path("/run/service-route-sync-clients.lock")


def load_targets(path):
    """Root-owned, private, bounded config; reject symlinks before reading."""
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o077:
        raise ObservationError("unsafe_configuration")
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ObservationError("unsafe_configuration")
        content = os.read(fd, 16385)
    finally:
        os.close(fd)
    if len(content) > 16384:
        raise ObservationError("invalid_configuration")
    raw = json.loads(content)
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema", "targets"}
        or type(raw["schema"]) is not int
        or raw["schema"] != 1
    ):
        raise ObservationError("invalid_configuration")
    validate_targets(raw["targets"])
    return raw["targets"]


def validate_targets(raw):
    if not isinstance(raw, dict) or not 1 <= len(raw) <= 16:
        raise ObservationError("invalid_client_targets")
    targets = {}
    for identity, value in raw.items():
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity):
            raise ObservationError("invalid_client_targets")
        if not isinstance(value, dict) or set(value) != {"address", "macs"}:
            raise ObservationError("invalid_client_targets")
        address = value["address"]
        if address is not None and not private_address(address):
            raise ObservationError("invalid_client_targets")
        macs = value["macs"]
        if not isinstance(macs, list) or not 1 <= len(macs) <= 16:
            raise ObservationError("invalid_client_targets")
        for mac in macs:
            if (
                not isinstance(mac, str)
                or not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac)
                or mac == "00:00:00:00:00:00"
                or int(mac[:2], 16) & 1
            ):
                raise ObservationError("invalid_client_targets")
        if len(set(macs)) != len(macs):
            raise ObservationError("ambiguous_client_targets")
        targets[identity] = {"address": address, "macs": tuple(macs)}
    all_macs = [mac for target in targets.values() for mac in target["macs"]]
    addresses = [target["address"] for target in targets.values() if target["address"] is not None]
    if len(set(addresses)) != len(addresses) or len(set(all_macs)) != len(all_macs):
        raise ObservationError("ambiguous_client_targets")
    return targets


def private_address(value):
    if not isinstance(value, str):
        return False
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return bool(
        any(address in network for network in PRIVATE_NETWORKS)
        and not any(
            (
                address.is_loopback,
                address.is_link_local,
                address.is_unspecified,
                address.is_multicast,
                address.is_reserved,
            )
        )
    )


def resolve_targets(targets, neighbors):
    """Derive addresses only from local neighbors matching administrator-owned MACs."""
    resolved = {}
    for identity, target in targets.items():
        if target["address"] is not None:
            resolved[identity] = target
            continue
        candidates = []
        if isinstance(neighbors, list):
            for row in neighbors:
                if not isinstance(row, dict) or not private_address(row.get("dst")):
                    continue
                candidate = {"address": row["dst"], "macs": target["macs"]}
                if neighbor_matches([row], candidate):
                    candidates.append(candidate)
        resolved[identity] = candidates[0] if len(candidates) == 1 else target
    addresses = [target["address"] for target in resolved.values() if target["address"] is not None]
    if len(set(addresses)) != len(addresses):
        raise ObservationError("ambiguous_client_targets")
    return resolved


def select_targets(targets, request):
    """Accept only private selections for fixed profiles; MAC checks remain mandatory."""
    if (
        not isinstance(request, dict)
        or set(request) != {"schema", "addresses"}
        or type(request["schema"]) is not int
        or request["schema"] != 1
    ):
        raise ObservationError("invalid_client_selection")
    addresses = request["addresses"]
    if not isinstance(addresses, dict) or set(addresses) != set(targets):
        raise ObservationError("invalid_client_selection")
    selected = {}
    for key, target in targets.items():
        address = addresses[key]
        if address is not None and not private_address(address):
            raise ObservationError("invalid_client_selection")
        # A configured fixed address cannot be widened by the caller.
        if target["address"] is not None and address != target["address"]:
            address = None
        selected[key] = {"address": address, "macs": target["macs"]}
    live = [row["address"] for row in selected.values() if row["address"] is not None]
    if len(live) != len(set(live)):
        raise ObservationError("ambiguous_client_targets")
    return selected


def read_selection():
    """An encrypted stdin frame, bounded in bytes and time; empty retains legacy mode."""
    content = bytearray()
    deadline = time.monotonic() + 2
    fd = sys.stdin.fileno()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise ObservationError("client_selection_timeout")
        chunk = os.read(fd, min(4096, 16385 - len(content)))
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > 16384:
            raise ObservationError("oversized_client_selection")
    if not content:
        return None

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ObservationError("invalid_client_selection")
            result[key] = value
        return result

    return json.loads(content, object_pairs_hook=unique)


def parse_ping(code, output, stderr):
    if code not in (0, 1) or stderr.strip():
        return None
    loss = re.findall(r"([\d.]+)% packet loss", output)
    rtt = re.findall(r"= ([\d.]+)/([\d.]+)/([\d.]+)/", output)
    if len(loss) != 1 or len(rtt) > 1:
        return None
    try:
        percent = float(loss[0])
        values = tuple(float(v) for v in rtt[0]) if rtt else None
    except ValueError:
        return None
    if not math.isfinite(percent) or not 0 <= percent <= 100:
        return None
    if values and (
        any(not math.isfinite(v) or v < 0 for v in values)
        or not values[0] <= values[1] <= values[2]
    ):
        return None
    if (code == 0 and (percent == 100 or values is None)) or (percent < 100 and values is None):
        return None
    return {
        "loss": percent,
        "average_ms": values[1] if values else None,
        "maximum_ms": values[2] if values else None,
    }


def parse_connections(code, output, source):
    if code != 0:
        return None
    counts = {key: 0 for key in ("dsl", "starlink", "vpn1", "vpn2", "other")}
    for line in output.splitlines():
        origins = re.findall(r"\bsrc=(\S+)", line)
        destinations = re.findall(r"\bdst=(\S+)", line)
        marks = re.findall(r"\bmark=(\d+)\b", line)
        if not origins or origins[0] != source or not destinations or len(marks) != 1:
            raise ObservationError("invalid_connection_response")
        try:
            destination = ipaddress.ip_address(destinations[0])
            mark = int(marks[0])
        except ValueError:
            raise ObservationError("invalid_connection_response") from None
        if not 0 <= mark <= 0xFFFFFFFF:
            raise ObservationError("invalid_connection_response")
        if destination.is_global and not destination.is_multicast:
            counts[ROUTES.get(mark & 0x3FFE0000, "other")] += 1
    return counts


def neighbor_matches(rows, target):
    """Missing, failed or ambiguous neighbor identities provide no evidence."""
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return False
    row = rows[0]
    states = row.get("state")
    return bool(
        row.get("dst") == target["address"]
        and row.get("lladdr") in target["macs"]
        and isinstance(states, list)
        and len(states) == 1
        and states[0] in {"REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT"}
    )


async def check_identity(target):
    try:
        code, output, _ = await run(
            ["ip", "-j", "-4", "neigh", "show", "to", target["address"]], timeout=2
        )
        return code == 0 and neighbor_matches(json.loads(output), target)
    except (OSError, ValueError, asyncio.TimeoutError, ObservationError):
        return False


def unknown_sample():
    return {
        "identity_verified": False,
        "probe_at_ms": None,
        "probe": None,
        "routing_at_ms": None,
        "connections": None,
    }


async def sample(identity, target, semaphore):
    async with semaphore:
        if target["address"] is None:
            return identity, unknown_sample()
        if not await check_identity(target):
            return identity, unknown_sample()
        address = target["address"]
        probe = None
        routes = None
        try:
            code, output, stderr = await run(
                ["ping", "-4", "-n", "-c", "3", "-i", "0.4", "-W", "1", address], timeout=5
            )
            probe = parse_ping(code, output.decode("ascii"), stderr.decode("ascii"))
        except (OSError, UnicodeError, asyncio.TimeoutError, ObservationError):
            pass
        probe_at = int(time.time() * 1000) if probe is not None else None
        try:
            code, output, _ = await run(
                ["conntrack", "-L", "-s", address, "-o", "extended"], timeout=4
            )
            routes = parse_connections(code, output.decode("ascii"), address)
        except (OSError, UnicodeError, asyncio.TimeoutError, ObservationError):
            pass
        if not await check_identity(target):
            return identity, unknown_sample()
        return identity, {
            "identity_verified": True,
            "probe_at_ms": probe_at,
            "probe": probe,
            "routing_at_ms": int(time.time() * 1000) if routes is not None else None,
            "connections": routes,
        }


async def observe_clients(configured_targets, selection=None):
    targets = validate_targets(configured_targets)
    if selection is not None:
        targets = select_targets(targets, selection)
    elif any(target["address"] is None for target in targets.values()):
        try:
            code, output, _ = await run(["ip", "-j", "-4", "neigh", "show"], timeout=2)
            neighbors = json.loads(output) if code == 0 else None
        except (OSError, ValueError, asyncio.TimeoutError, ObservationError):
            neighbors = None
        targets = resolve_targets(targets, neighbors)
    semaphore = asyncio.Semaphore(4)
    return dict(
        await asyncio.gather(*(sample(key, address, semaphore) for key, address in targets.items()))
    )


def main():
    if os.environ.get("SSH_ORIGINAL_COMMAND") != COMMAND:
        print(json.dumps({"schema": 1, "error_code": "command_denied"}))
        return 2
    lock = None
    try:
        lock = os.open(str(LOCK_PATH), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ObservationError("unsafe_runtime_state")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ObservationError("busy") from None
        previous = os.read(lock, 128)
        now = time.monotonic()
        if previous:
            last = float(previous)
            if not math.isfinite(last):
                raise ObservationError("invalid_runtime_state")
            if 0 <= now - last < 5:
                raise ObservationError("rate_limited")
        targets = load_targets(CONFIG_PATH)
        selection = read_selection()
        os.lseek(lock, 0, 0)
        os.ftruncate(lock, 0)
        os.write(lock, str(now).encode())

        async def bounded():
            # Four groups of at most four bounded probes; cancellation kills children.
            return await asyncio.wait_for(observe_clients(targets, selection), 55)

        result = {"schema": 1, "observed_at_ms": None, "clients": asyncio.run(bounded())}
        result["observed_at_ms"] = int(time.time() * 1000)
        output = json.dumps(result, separators=(",", ":"))
        if len(output.encode()) > 32768:
            raise ObservationError("oversized_response")
        print(output)
        return 0
    except ObservationError as exc:
        print(json.dumps({"schema": 1, "error_code": str(exc)}))
        return 1
    except Exception:
        print(json.dumps({"schema": 1, "error_code": "observation_failed"}))
        return 1
    finally:
        if lock is not None:
            os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
