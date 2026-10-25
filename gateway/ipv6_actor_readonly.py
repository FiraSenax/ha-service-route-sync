#!/usr/bin/python3
"""Restricted gateway IPv6 policy actor. Raw UDAPI/rules never leave the router."""

import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ipv6_policy import (
    PolicyError,
    PolicySnapshot,
    classify,
    expected_sab,
    normalized_policy,
    owned_mode,
    parse_config,
)
from observer import delegation


COMMAND = "service-route-sync ipv6 v1"
ROOT = Path("/data/service-route-sync")
CONFIG = ROOT / "ipv6_actor.json"
JOURNAL = ROOT / "ipv6_actor_state.json"
LOCK = Path("/run/service-route-sync-ipv6.lock")
ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}


def private_json(path, *, required=True):
    try:
        directory, info = path.parent.lstat(), path.lstat()
    except FileNotFoundError:
        if not required:
            return None
        raise PolicyError("missing_private_state") from None
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != 0
        or directory.st_mode & 0o077
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & 0o077
    ):
        raise PolicyError("unsafe_private_state")
    with path.open("rb") as source:
        value = source.read(65537)
    if len(value) > 65536:
        raise PolicyError("oversized_private_state")
    try:
        return json.loads(value)
    except (ValueError, UnicodeError):
        raise PolicyError("invalid_private_state") from None


def command(args, *, allow_absent=False, maximum=524288):
    try:
        result = subprocess.run(args, capture_output=True, timeout=8, env=ENVIRONMENT)
    except (OSError, subprocess.TimeoutExpired):
        raise PolicyError("gateway_command_failed") from None
    if len(result.stdout) > maximum or len(result.stderr) > 4096:
        raise PolicyError("gateway_response_oversized")
    if result.returncode != 0 and not (allow_absent and result.returncode == 1):
        raise PolicyError("gateway_command_failed")
    try:
        return result.stdout.decode("utf-8"), result.returncode
    except UnicodeError:
        raise PolicyError("gateway_response_invalid") from None


def managed_chain(tool, table, chain):
    raw, code = command([tool, "-w", "2", "-t", table, "-S", chain], allow_absent=True)
    return tuple(raw.splitlines()) if code == 0 else ()


def hook_lines(tool, table, chain, target):
    raw, _ = command([tool, "-w", "2", "-t", table, "-S", chain])
    return tuple(line for line in raw.splitlines() if target in line)


def current_delegation(config):
    raw, _ = command(["ubios-udapi-client", "-r", "-t", "3", "GET", "/interfaces"])
    try:
        rows = json.loads(raw)
    except (ValueError, UnicodeError):
        raise PolicyError("gateway_observation_invalid") from None
    if not isinstance(rows, list):
        raise PolicyError("gateway_observation_invalid")
    selected = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("identification"), dict)
        and row["identification"].get("id") == config.primary_interface
    ]
    if len(selected) != 1:
        raise PolicyError("ambiguous_primary_interface")
    known, prefix = delegation(selected[0], 56)
    if not known:
        raise PolicyError("delegation_unknown")
    return prefix


def default_route(table, interface):
    raw, _ = command(["ip", "-j", "-6", "route", "show", "table", table, "default"])
    try:
        rows = json.loads(raw)
    except (ValueError, UnicodeError):
        raise PolicyError("route_observation_invalid") from None
    if not isinstance(rows, list):
        raise PolicyError("route_observation_invalid")
    return any(
        isinstance(row, dict)
        and row.get("dev") == interface
        and row.get("dst") == "default"
        and row.get("type") not in ("unreachable", "blackhole", "prohibit", "throw")
        for row in rows
    )


def snapshot(config):
    try:
        delegation_value = current_delegation(config)
    except PolicyError as error:
        if str(error) != "delegation_unknown":
            raise
        delegation_value = None
    policy, _ = command(["ip", "-6", "rule", "show", "pref", "31010"])
    temporary, _ = command(["ip", "-6", "rule", "show", "pref", "31009"])
    return PolicySnapshot(
        managed_chain("ip6tables", "nat", "IOB_V6_NAT"),
        hook_lines("ip6tables", "nat", "UBIOS_POSTROUTING_USER_HOOK", "IOB_V6_NAT"),
        managed_chain("iptables", "mangle", "IOB_SAB_PBR"),
        hook_lines("iptables", "mangle", "UBIOS_PREROUTING_PBR", "IOB_SAB_PBR"),
        normalized_policy(policy.splitlines(), 31010),
        normalized_policy(temporary.splitlines(), 31009),
        delegation_value,
    )


def request():
    raw = sys.stdin.buffer.read(1025)
    if not raw or len(raw) > 1024:
        raise PolicyError("invalid_request_size")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise PolicyError("invalid_request") from None
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "operation"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or value["operation"] != "inspect"
    ):
        raise PolicyError("operation_denied")
    return value


def blank(snapshot_value):
    return not any(
        (
            snapshot_value.nat_chain,
            snapshot_value.nat_hook,
            snapshot_value.sab_chain,
            snapshot_value.sab_hook,
            snapshot_value.policy,
            snapshot_value.temporary_policy,
        )
    )


def hooks_only_missing(snapshot_value, config):
    if snapshot_value.temporary_policy or snapshot_value.sab_chain != expected_sab(config):
        return False
    mode, _ = owned_mode(
        PolicySnapshot(
            snapshot_value.nat_chain,
            ("-A UBIOS_POSTROUTING_USER_HOOK -j IOB_V6_NAT",),
            snapshot_value.sab_chain,
            ("-A UBIOS_PREROUTING_PBR -j IOB_SAB_PBR",),
            snapshot_value.policy,
            (),
            snapshot_value.delegation,
        ),
        config,
    )
    return mode != "unknown" and (
        snapshot_value.nat_hook in ((), ("-A UBIOS_POSTROUTING_USER_HOOK -j IOB_V6_NAT",))
        and snapshot_value.sab_hook in ((), ("-A UBIOS_PREROUTING_PBR -j IOB_SAB_PBR",))
    )


def main():
    if os.environ.get("SSH_ORIGINAL_COMMAND") != COMMAND:
        raise PolicyError("command_denied")
    request()
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = parse_config(private_json(CONFIG))
        journal = private_json(JOURNAL, required=False)
        if journal is not None and (
            not isinstance(journal, dict)
            or journal.get("phase") not in ("complete", "intent", "blocked")
        ):
            raise PolicyError("review_required")
        observed = snapshot(config)
        mode = classify(observed, config)
        recognized, _ = owned_mode(observed, config)
        return {
            "schema": 1,
            "operation": "inspect",
            "mode": mode,
            "writable": False,
            "owned_mode": recognized,
            "repairable": bool(
                recognized != "unknown" or blank(observed) or hooks_only_missing(observed, config)
            ),
            "blank": blank(observed),
            "healthy": mode in ("primary", "secondary"),
            "digest": observed.digest(),
            "delegation_ready": observed.delegation is not None,
            "primary_default": default_route(config.primary_table, config.primary_interface),
            "secondary_default": default_route(config.secondary_table, config.secondary_interface),
            "review_required": journal is not None
            and journal.get("phase") in ("intent", "blocked"),
        }
    except BlockingIOError:
        raise PolicyError("busy") from None
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        print(json.dumps(main(), separators=(",", ":")))
    except PolicyError as error:
        print(json.dumps({"schema": 1, "error_code": str(error)}, separators=(",", ":")))
        raise SystemExit(1)
    except Exception:
        print('{"schema":1,"error_code":"actor_failed"}')
        raise SystemExit(1)
