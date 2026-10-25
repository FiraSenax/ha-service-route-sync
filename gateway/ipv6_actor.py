#!/usr/bin/python3
"""Restricted gateway IPv6 policy actor. Raw UDAPI/rules never leave the router."""

import fcntl
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ipv6_policy import (
    PolicyError,
    PolicySnapshot,
    classify,
    expected_nat,
    expected_policy,
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
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1:
        raise PolicyError("operation_denied")
    if value.get("operation") == "inspect" and set(value) == {"schema", "operation"}:
        return value
    if value.get("operation") != "apply" or set(value) != {
        "schema",
        "operation",
        "transaction_id",
        "expected_digest",
        "mode",
    }:
        raise PolicyError("operation_denied")
    if (
        not isinstance(value["transaction_id"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", value["transaction_id"])
        or not isinstance(value["expected_digest"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["expected_digest"])
        or value["mode"] not in ("primary", "secondary")
    ):
        raise PolicyError("operation_denied")
    return value


def atomic_journal(value):
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    if len(encoded) > 16384:
        raise PolicyError("journal_oversized")
    temporary = JOURNAL.with_name(JOURNAL.name + f".{os.getpid()}.tmp")
    fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, JOURNAL)
    directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def rule_command(tool, table, line, action):
    parts = shlex.split(line)
    if not parts or parts[0] != "-A" or action not in ("-A", "-D", "-I"):
        raise PolicyError("invalid_managed_rule")
    return [tool, "-w", "5", "-t", table, action, *parts[1:]]


def insert_rule(tool, table, line):
    """iptables requires the insertion index after the chain name."""
    args = rule_command(tool, table, line, "-I")
    return [*args[:7], "1", *args[7:]]


def policy_command(action, priority, source, table):
    if (
        action not in ("add", "del")
        or priority not in (31009, 31010)
        or not isinstance(table, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", table)
    ):
        raise PolicyError("invalid_managed_policy")
    return ["ip", "-6", "rule", action, "pref", str(priority), "from", source, "lookup", table]


def policy_parts(line, priority, config):
    match = re.fullmatch(rf"{priority}: from (\S+) lookup (\S+)", line)
    if (
        not match
        or match[1] != str(config.ula)
        or match[2] not in (config.primary_table, config.secondary_table)
    ):
        raise PolicyError("unknown_policy_rule")
    return match[1], match[2]


def expected_mutation(before, args):
    """Calculate the single managed edit; reject a command-shaped surprise."""
    if args[:3] == ["ip", "-6", "rule"] and len(args) == 10:
        action, priority, source, table = args[3], args[5], args[7], args[9]
        if (args[4], args[6], args[8]) != ("pref", "from", "lookup") or action not in (
            "add",
            "del",
        ):
            raise PolicyError("invalid_managed_rule")
        field = {"31009": "temporary_policy", "31010": "policy"}.get(priority)
        if field is None:
            raise PolicyError("invalid_managed_rule")
        line = f"{priority}: from {source} lookup {table}"
        rows = list(getattr(before, field))
        if action == "add":
            if line in rows:
                raise PolicyError("duplicate_managed_rule")
            rows.append(line)
        else:
            if rows.count(line) != 1:
                raise PolicyError("missing_managed_rule")
            rows.remove(line)
        return replace(before, **{field: tuple(rows)})
    if len(args) < 7 or args[0] not in ("ip6tables", "iptables") or args[3] != "-t":
        raise PolicyError("invalid_managed_rule")
    tool, table, action, chain = args[0], args[4], args[5], args[6]
    scopes = {
        ("ip6tables", "nat", "IOB_V6_NAT"): "nat_chain",
        ("ip6tables", "nat", "UBIOS_POSTROUTING_USER_HOOK"): "nat_hook",
        ("iptables", "mangle", "IOB_SAB_PBR"): "sab_chain",
        ("iptables", "mangle", "UBIOS_PREROUTING_PBR"): "sab_hook",
    }
    field = scopes.get((tool, table, chain))
    if field is None:
        raise PolicyError("invalid_managed_rule")
    rows = list(getattr(before, field))
    if action == "-N" and len(args) == 7 and field.endswith("chain") and not rows:
        rows = [f"-N {chain}"]
    elif (
        action == "-F"
        and len(args) == 7
        and field.endswith("chain")
        and rows[:1] == [f"-N {chain}"]
    ):
        rows = rows[:1]
    elif action == "-X" and len(args) == 7 and rows == [f"-N {chain}"]:
        rows = []
    elif action in ("-A", "-D", "-I"):
        if action == "-I":
            if len(args) < 9 or args[7] != "1":
                raise PolicyError("invalid_managed_rule")
            suffix = args[8:]
        else:
            suffix = args[7:]
        line = " ".join(("-A", chain, *suffix))
        if action == "-D":
            if rows.count(line) != 1:
                raise PolicyError("missing_managed_rule")
            rows.remove(line)
        elif line in rows:
            raise PolicyError("duplicate_managed_rule")
        elif action == "-I":
            rows.insert(0 if field.endswith("hook") else 1, line)
        else:
            rows.append(line)
    else:
        raise PolicyError("invalid_managed_rule")
    return replace(before, **{field: tuple(rows)})


def mutate(config, args):
    before = snapshot(config)
    expected = expected_mutation(before, args)
    command(args)
    if snapshot(config) != expected:
        raise PolicyError("post_command_mismatch")


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
    mode, mapped = owned_mode(
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


def ensure_target(config, mode, observed):
    if mode == "primary":
        expected_nat(config, "primary", observed.delegation)
        if not default_route(config.primary_table, config.primary_interface):
            raise PolicyError("target_route_unavailable")
    elif not default_route(config.secondary_table, config.secondary_interface):
        raise PolicyError("target_route_unavailable")


def append_expected(config, mode, delegation_value):
    for line in expected_nat(config, mode, delegation_value)[1:]:
        mutate(config, rule_command("ip6tables", "nat", line, "-A"))


def switch_exact(config, before, mode, old_mode):
    if mode == old_mode:
        # A delegation rotation changes only the NETMAP target. Insert first
        # so the policy never points to an untranslated interface.
        old_rule = before.nat_chain[1]
        new_rule = expected_nat(config, mode, before.delegation)[1]
        if old_rule == new_rule:
            return
        mutate(config, insert_rule("ip6tables", "nat", new_rule))
        mutate(config, rule_command("ip6tables", "nat", old_rule, "-D"))
        return
    append_expected(config, mode, before.delegation)
    target = config.primary_table if mode == "primary" else config.secondary_table
    old = config.primary_table if old_mode == "primary" else config.secondary_table
    mutate(config, policy_command("add", 31009, str(config.ula), target))
    mutate(config, policy_command("del", 31010, str(config.ula), old))
    mutate(config, policy_command("add", 31010, str(config.ula), target))
    mutate(config, policy_command("del", 31009, str(config.ula), target))
    mutate(config, rule_command("ip6tables", "nat", before.nat_chain[1], "-D"))


def rebuild_blank(config, observed, mode):
    if not blank(observed):
        raise PolicyError("unknown_managed_state")
    mutate(config, ["ip6tables", "-w", "5", "-t", "nat", "-N", "IOB_V6_NAT"])
    append_expected(config, mode, observed.delegation)
    mutate(config, ["iptables", "-w", "5", "-t", "mangle", "-N", "IOB_SAB_PBR"])
    for line in expected_sab(config)[1:]:
        mutate(config, rule_command("iptables", "mangle", line, "-A"))
    mutate(config, insert_rule("ip6tables", "nat", "-A UBIOS_POSTROUTING_USER_HOOK -j IOB_V6_NAT"))
    mutate(config, insert_rule("iptables", "mangle", "-A UBIOS_PREROUTING_PBR -j IOB_SAB_PBR"))
    target = config.primary_table if mode == "primary" else config.secondary_table
    mutate(config, policy_command("add", 31010, str(config.ula), target))


def repair_hooks(config, before):
    if not hooks_only_missing(before, config):
        raise PolicyError("unknown_managed_state")
    if not before.nat_hook:
        mutate(
            config,
            [
                "ip6tables",
                "-w",
                "5",
                "-t",
                "nat",
                "-I",
                "UBIOS_POSTROUTING_USER_HOOK",
                "1",
                "-j",
                "IOB_V6_NAT",
            ],
        )
    if not before.sab_hook:
        mutate(
            config,
            [
                "iptables",
                "-w",
                "5",
                "-t",
                "mangle",
                "-I",
                "UBIOS_PREROUTING_PBR",
                "1",
                "-j",
                "IOB_SAB_PBR",
            ],
        )


def restore(config, before, current, desired):
    """Rollback only if every observed intermediate rule belongs to this transaction."""
    old_mode, _ = owned_mode(before, config)
    permitted_nat = set(before.nat_chain[1:]) | set(
        expected_nat(config, desired, before.delegation)[1:]
    )
    expected_sab_rules = expected_sab(config)
    sab_is_owned_prefix = current.sab_chain == expected_sab_rules[: len(current.sab_chain)]
    target_policy = expected_policy(config, desired)
    target_temporary = (target_policy[0].replace("31010:", "31009:", 1),)
    permitted_policy = (before.policy, (), target_policy)
    if (
        any(line not in permitted_nat for line in current.nat_chain[1:])
        or len(current.nat_chain[1:]) != len(set(current.nat_chain[1:]))
        or current.nat_chain[:1] not in ((), ("-N IOB_V6_NAT",))
        or not sab_is_owned_prefix
        or current.delegation != before.delegation
        or current.policy not in permitted_policy
        or current.temporary_policy not in ((), target_temporary)
        or current.nat_hook not in ((), ("-A UBIOS_POSTROUTING_USER_HOOK -j IOB_V6_NAT",))
        or current.sab_hook not in ((), ("-A UBIOS_PREROUTING_PBR -j IOB_SAB_PBR",))
    ):
        raise PolicyError("rollback_state_ambiguous")
    for line in current.temporary_policy:
        source, table = policy_parts(line, 31009, config)
        mutate(config, policy_command("del", 31009, source, table))
    for line in current.policy:
        source, table = policy_parts(line, 31010, config)
        mutate(config, policy_command("del", 31010, source, table))
    for line in current.nat_hook:
        mutate(config, rule_command("ip6tables", "nat", line, "-D"))
    for line in current.sab_hook:
        mutate(config, rule_command("iptables", "mangle", line, "-D"))
    if current.nat_chain:
        mutate(config, ["ip6tables", "-w", "5", "-t", "nat", "-F", "IOB_V6_NAT"])
    if current.sab_chain:
        mutate(config, ["iptables", "-w", "5", "-t", "mangle", "-F", "IOB_SAB_PBR"])
    if before.nat_chain:
        if not current.nat_chain:
            mutate(config, ["ip6tables", "-w", "5", "-t", "nat", "-N", "IOB_V6_NAT"])
        for line in before.nat_chain[1:]:
            mutate(config, rule_command("ip6tables", "nat", line, "-A"))
    elif current.nat_chain:
        mutate(config, ["ip6tables", "-w", "5", "-t", "nat", "-X", "IOB_V6_NAT"])
    if before.sab_chain:
        if not current.sab_chain:
            mutate(config, ["iptables", "-w", "5", "-t", "mangle", "-N", "IOB_SAB_PBR"])
        for line in before.sab_chain[1:]:
            mutate(config, rule_command("iptables", "mangle", line, "-A"))
    elif current.sab_chain:
        mutate(config, ["iptables", "-w", "5", "-t", "mangle", "-X", "IOB_SAB_PBR"])
    for line in before.policy:
        source, table = policy_parts(line, 31010, config)
        mutate(config, policy_command("add", 31010, source, table))
    for line in before.nat_hook:
        mutate(
            config,
            [
                "ip6tables",
                "-w",
                "5",
                "-t",
                "nat",
                "-I",
                "UBIOS_POSTROUTING_USER_HOOK",
                "1",
                "-j",
                "IOB_V6_NAT",
            ],
        )
    for line in before.sab_hook:
        mutate(
            config,
            [
                "iptables",
                "-w",
                "5",
                "-t",
                "mangle",
                "-I",
                "UBIOS_PREROUTING_PBR",
                "1",
                "-j",
                "IOB_SAB_PBR",
            ],
        )


def apply(config, call, before, journal):
    if journal is not None and journal.get("phase") in ("intent", "blocked"):
        raise PolicyError("review_required")
    if before.digest() != call["expected_digest"]:
        raise PolicyError("state_changed")
    current_mode = classify(before, config)
    if current_mode == call["mode"]:
        return {
            "schema": 1,
            "operation": "apply",
            "applied": False,
            "mode": current_mode,
            "digest": before.digest(),
            "review_required": False,
        }
    old_mode, _ = owned_mode(before, config)
    if old_mode == "unknown" and not blank(before) and not hooks_only_missing(before, config):
        raise PolicyError("unknown_managed_state")
    ensure_target(config, call["mode"], before)
    atomic_journal(
        {
            "phase": "intent",
            "transaction_id": call["transaction_id"],
            "before_digest": before.digest(),
            "before": asdict(before),
            "desired": call["mode"],
        }
    )
    try:
        if old_mode != "unknown":
            switch_exact(config, before, call["mode"], old_mode)
        elif blank(before):
            rebuild_blank(config, before, call["mode"])
        elif hooks_only_missing(before, config):
            repair_hooks(config, before)
            repaired = snapshot(config)
            repaired_mode, _ = owned_mode(repaired, config)
            if repaired_mode != call["mode"] or classify(repaired, config) != call["mode"]:
                switch_exact(config, repaired, call["mode"], repaired_mode)
        else:
            raise PolicyError("unknown_managed_state")
        after = snapshot(config)
        if classify(after, config) != call["mode"]:
            raise PolicyError("post_apply_mismatch")
        atomic_journal(
            {
                "phase": "complete",
                "transaction_id": call["transaction_id"],
                "mode": call["mode"],
                "digest": after.digest(),
            }
        )
        return {
            "schema": 1,
            "operation": "apply",
            "applied": True,
            "mode": call["mode"],
            "digest": after.digest(),
            "review_required": False,
        }
    except Exception:
        try:
            restore(config, before, snapshot(config), call["mode"])
            if snapshot(config).digest() != before.digest():
                raise PolicyError("rollback_mismatch")
            atomic_journal(
                {
                    "phase": "complete",
                    "transaction_id": call["transaction_id"],
                    "mode": old_mode,
                    "digest": before.digest(),
                    "rolled_back": True,
                }
            )
        except Exception:
            atomic_journal(
                {
                    "phase": "blocked",
                    "transaction_id": call["transaction_id"],
                    "before_digest": before.digest(),
                    "desired": call["mode"],
                }
            )
            raise PolicyError("review_required") from None
        raise PolicyError("apply_rolled_back") from None


def main():
    if os.environ.get("SSH_ORIGINAL_COMMAND") != COMMAND:
        raise PolicyError("command_denied")
    call = request()
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
        if call["operation"] == "apply":
            return apply(config, call, observed, journal)
        mode = classify(observed, config)
        recognized, _ = owned_mode(observed, config)
        return {
            "schema": 1,
            "operation": "inspect",
            "mode": mode,
            "writable": True,
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
