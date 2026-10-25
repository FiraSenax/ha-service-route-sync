"""Closed representation of the two legacy managed gateway policy scopes."""

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import re


class PolicyError(ValueError):
    """A sanitized refusal; never include public prefixes or rule text."""


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    ula: ipaddress.IPv6Network
    primary_interface: str
    secondary_interface: str
    primary_table: str
    secondary_table: str
    primary_mark: str
    secondary_mark: str
    sab_primary_ip: ipaddress.IPv4Address
    sab_secondary_ip: ipaddress.IPv4Address


def parse_config(raw):
    fields = {
        "schema",
        "ula_prefix",
        "primary_interface",
        "secondary_interface",
        "primary_table",
        "secondary_table",
        "primary_mark",
        "secondary_mark",
        "sab_primary_ip",
        "sab_secondary_ip",
    }
    if (
        not isinstance(raw, dict)
        or set(raw) != fields
        or type(raw["schema"]) is not int
        or raw["schema"] != 1
    ):
        raise PolicyError("invalid_configuration")
    try:
        ula = ipaddress.IPv6Network(raw["ula_prefix"], strict=True)
        primary_ip = ipaddress.IPv4Address(raw["sab_primary_ip"])
        star_ip = ipaddress.IPv4Address(raw["sab_secondary_ip"])
    except (ValueError, TypeError):
        raise PolicyError("invalid_configuration") from None
    if (
        not ula.is_private
        or not ula.subnet_of(ipaddress.IPv6Network("fc00::/7"))
        or not 48 <= ula.prefixlen <= 64
        or ula.is_multicast
        or primary_ip == star_ip
        or not primary_ip.is_private
        or not star_ip.is_private
    ):
        raise PolicyError("invalid_configuration")
    for key in ("primary_interface", "secondary_interface"):
        if (
            not isinstance(raw[key], str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,14}", raw[key])
            or raw[key] == "lo"
        ):
            raise PolicyError("invalid_configuration")
    for key in ("primary_table", "secondary_table"):
        if not isinstance(raw[key], str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", raw[key]
        ):
            raise PolicyError("invalid_configuration")
    if (
        raw["primary_interface"] == raw["secondary_interface"]
        or raw["primary_table"] == raw["secondary_table"]
    ):
        raise PolicyError("invalid_configuration")
    marks = []
    for key in ("primary_mark", "secondary_mark"):
        value = raw[key]
        if not isinstance(value, str) or not re.fullmatch(
            r"0x[0-9a-f]{1,8}/0x[0-9a-f]{1,8}", value
        ):
            raise PolicyError("invalid_configuration")
        mark, mask = (int(part, 16) for part in value.split("/"))
        if not mark or not mask or mark & ~mask:
            raise PolicyError("invalid_configuration")
        marks.append((mark, mask))
    if marks[0][0] == marks[1][0] or marks[0][1] != marks[1][1]:
        raise PolicyError("invalid_configuration")
    return PolicyConfig(
        ula,
        raw["primary_interface"],
        raw["secondary_interface"],
        raw["primary_table"],
        raw["secondary_table"],
        raw["primary_mark"],
        raw["secondary_mark"],
        primary_ip,
        star_ip,
    )


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    nat_chain: tuple[str, ...]
    nat_hook: tuple[str, ...]
    sab_chain: tuple[str, ...]
    sab_hook: tuple[str, ...]
    policy: tuple[str, ...]
    temporary_policy: tuple[str, ...]
    delegation: str | None

    def digest(self):
        value = {name: getattr(self, name) for name in self.__dataclass_fields__}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def expected_nat(config, mode, delegation):
    source = str(config.ula)
    if mode == "primary":
        try:
            prefix = ipaddress.IPv6Network(delegation, strict=True)
        except (TypeError, ValueError):
            raise PolicyError("delegation_unavailable") from None
        if not prefix.is_global or prefix.prefixlen != config.ula.prefixlen:
            raise PolicyError("delegation_unavailable")
        return (
            "-N IOB_V6_NAT",
            f"-A IOB_V6_NAT -s {source} -o {config.primary_interface} -j NETMAP --to {prefix}",
        )
    if mode == "secondary":
        return (
            "-N IOB_V6_NAT",
            f"-A IOB_V6_NAT -s {source} -o {config.secondary_interface} -j MASQUERADE",
        )
    raise PolicyError("invalid_mode")


def expected_sab(config):
    rules = ["-N IOB_SAB_PBR"]
    for address, mark in (
        (config.sab_secondary_ip, config.secondary_mark),
        (config.sab_primary_ip, config.primary_mark),
    ):
        source = f"{address}/32"
        mask = mark.split("/")[1]
        rules.extend(
            (
                f"-A IOB_SAB_PBR -s {source} -j MARK --set-xmark {mark}",
                f"-A IOB_SAB_PBR -s {source} -j CONNMARK --save-mark --nfmask {mask} --ctmask {mask}",
                f"-A IOB_SAB_PBR -s {source} -j RETURN",
            )
        )
    return tuple(rules)


def expected_policy(config, mode):
    table = config.primary_table if mode == "primary" else config.secondary_table
    if mode not in ("primary", "secondary"):
        raise PolicyError("invalid_mode")
    return (f"31010: from {config.ula} lookup {table}",)


def normalized_policy(lines, priority):
    result = []
    # Do not erase selectors such as fwmark, iif, to, suppress_prefixlength or
    # protocol. They are distinct rules even when source and table coincide.
    pattern = re.compile(rf"^{priority}:\s+from (\S+) lookup (\S+)$")
    for line in lines:
        match = pattern.match(line.strip())
        if not match:
            raise PolicyError("unknown_policy_rule")
        result.append(f"{priority}: from {match[1]} lookup {match[2]}")
    return tuple(result)


def classify(snapshot, config):
    """Accept only a complete, exact single-owner policy; unknown means no write."""
    mode, mapped_prefix = owned_mode(snapshot, config)
    if mode == "primary" and mapped_prefix != snapshot.delegation:
        return "unknown"
    return mode


def owned_mode(snapshot, config):
    """Recognize exact old ownership even when its delegated prefix rotated."""
    if (
        snapshot.temporary_policy
        or snapshot.nat_hook != ("-A UBIOS_POSTROUTING_USER_HOOK -j IOB_V6_NAT",)
        or snapshot.sab_hook != ("-A UBIOS_PREROUTING_PBR -j IOB_SAB_PBR",)
        or snapshot.sab_chain != expected_sab(config)
    ):
        return "unknown", None
    if snapshot.nat_chain == expected_nat(
        config, "secondary", None
    ) and snapshot.policy == expected_policy(config, "secondary"):
        return "secondary", None
    if (
        len(snapshot.nat_chain) == 2
        and snapshot.nat_chain[0] == "-N IOB_V6_NAT"
        and snapshot.policy == expected_policy(config, "primary")
    ):
        match = re.fullmatch(
            rf"-A IOB_V6_NAT -s (\S+) -o {re.escape(config.primary_interface)} -j NETMAP --to (\S+)",
            snapshot.nat_chain[1],
        )
        if match and match[1] == str(config.ula):
            try:
                mapped = ipaddress.IPv6Network(match[2], strict=True)
            except ValueError:
                mapped = None
            if mapped is not None and mapped.is_global and mapped.prefixlen == config.ula.prefixlen:
                return "primary", str(mapped)
    return "unknown", None


def recoverable(snapshot, config):
    """Only missing pieces of our exact policy may be rebuilt automatically."""
    if snapshot.temporary_policy:
        return False
    if snapshot.nat_hook not in ((), ("-A UBIOS_POSTROUTING_USER_HOOK -j IOB_V6_NAT",)):
        return False
    if snapshot.sab_hook not in ((), ("-A UBIOS_PREROUTING_PBR -j IOB_SAB_PBR",)):
        return False
    if snapshot.sab_chain not in ((), expected_sab(config)):
        return False
    known_nat = snapshot.nat_chain in ((), expected_nat(config, "secondary", None))
    nat_mode = (
        "secondary" if snapshot.nat_chain == expected_nat(config, "secondary", None) else None
    )
    if not known_nat and len(snapshot.nat_chain) == 2 and snapshot.nat_chain[0] == "-N IOB_V6_NAT":
        match = re.fullmatch(
            rf"-A IOB_V6_NAT -s (\S+) -o {re.escape(config.primary_interface)} -j NETMAP --to (\S+)",
            snapshot.nat_chain[1],
        )
        if match and match[1] == str(config.ula):
            try:
                mapped = ipaddress.IPv6Network(match[2], strict=True)
                known_nat = mapped.is_global and mapped.prefixlen == config.ula.prefixlen
                nat_mode = "primary" if known_nat else None
            except ValueError:
                pass
    if not known_nat:
        return False
    if snapshot.policy not in (
        (),
        expected_policy(config, "primary"),
        expected_policy(config, "secondary"),
    ):
        return False
    if nat_mode and snapshot.policy and snapshot.policy != expected_policy(config, nat_mode):
        return False
    # A populated hook pointing into a missing chain is an ambiguous edit,
    # not an ordinary UniFi reboot loss.
    if snapshot.nat_hook and not snapshot.nat_chain:
        return False
    if snapshot.sab_hook and not snapshot.sab_chain:
        return False
    return True
