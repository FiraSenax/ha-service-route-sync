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
    dsl_table: str
    starlink_table: str
    dsl_mark: str
    starlink_mark: str
    sab_dsl_ip: ipaddress.IPv4Address
    sab_starlink_ip: ipaddress.IPv4Address


def parse_config(raw):
    fields = {"schema", "ula_prefix", "dsl_table", "starlink_table", "dsl_mark",
              "starlink_mark", "sab_dsl_ip", "sab_starlink_ip"}
    if not isinstance(raw, dict) or set(raw) != fields or type(raw["schema"]) is not int or raw["schema"] != 1:
        raise PolicyError("invalid_configuration")
    try:
        ula = ipaddress.IPv6Network(raw["ula_prefix"], strict=True)
        dsl_ip = ipaddress.IPv4Address(raw["sab_dsl_ip"])
        star_ip = ipaddress.IPv4Address(raw["sab_starlink_ip"])
    except (ValueError, TypeError):
        raise PolicyError("invalid_configuration") from None
    if (not ula.is_private or ula.prefixlen != 56 or ula.is_multicast or dsl_ip == star_ip
            or not dsl_ip.is_private or not star_ip.is_private):
        raise PolicyError("invalid_configuration")
    for key, expected in (("dsl_table", "201.ppp0"),
                          ("starlink_table", "202.eth1")):
        if raw[key] != expected:
            raise PolicyError("invalid_configuration")
    for key in ("dsl_mark", "starlink_mark"):
        if (not isinstance(raw[key], str) or not re.fullmatch(r"0x[0-9a-f]{1,8}/0x7e0000", raw[key])
                or not 0 < int(raw[key].split("/")[0], 16) <= 0x7e0000
                or int(raw[key].split("/")[0], 16) & ~0x7e0000):
            raise PolicyError("invalid_configuration")
    if raw["dsl_mark"] == raw["starlink_mark"]:
        raise PolicyError("invalid_configuration")
    return PolicyConfig(ula, raw["dsl_table"], raw["starlink_table"],
                        raw["dsl_mark"], raw["starlink_mark"], dsl_ip, star_ip)


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
    if mode == "dsl":
        try:
            prefix = ipaddress.IPv6Network(delegation, strict=True)
        except (TypeError, ValueError):
            raise PolicyError("delegation_unavailable") from None
        if not prefix.is_global or prefix.prefixlen != 56:
            raise PolicyError("delegation_unavailable")
        return ("-N IOB_V6_NAT",
                f"-A IOB_V6_NAT -s {source} -o ppp0 -j NETMAP --to {prefix}")
    if mode == "starlink":
        return ("-N IOB_V6_NAT",
                f"-A IOB_V6_NAT -s {source} -o eth1 -j MASQUERADE")
    raise PolicyError("invalid_mode")


def expected_sab(config):
    rules = ["-N IOB_SAB_PBR"]
    for address, mark in ((config.sab_starlink_ip, config.starlink_mark),
                          (config.sab_dsl_ip, config.dsl_mark)):
        source = f"{address}/32"
        rules.extend((
            f"-A IOB_SAB_PBR -s {source} -j MARK --set-xmark {mark}",
            f"-A IOB_SAB_PBR -s {source} -j CONNMARK --save-mark --nfmask 0x7e0000 --ctmask 0x7e0000",
            f"-A IOB_SAB_PBR -s {source} -j RETURN",
        ))
    return tuple(rules)


def expected_policy(config, mode):
    table = config.dsl_table if mode == "dsl" else config.starlink_table
    if mode not in ("dsl", "starlink"):
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
    if mode == "dsl" and mapped_prefix != snapshot.delegation:
        return "unknown"
    return mode


def owned_mode(snapshot, config):
    """Recognize exact old ownership even when its delegated prefix rotated."""
    if snapshot.temporary_policy or snapshot.nat_hook != (
        "-A UBIOS_POSTROUTING_USER_HOOK -j IOB_V6_NAT",) or snapshot.sab_hook != (
        "-A UBIOS_PREROUTING_PBR -j IOB_SAB_PBR",) or snapshot.sab_chain != expected_sab(config):
        return "unknown", None
    if snapshot.nat_chain == expected_nat(config, "starlink", None) and snapshot.policy == expected_policy(config, "starlink"):
        return "starlink", None
    if len(snapshot.nat_chain) == 2 and snapshot.nat_chain[0] == "-N IOB_V6_NAT" and snapshot.policy == expected_policy(config, "dsl"):
        match = re.fullmatch(r"-A IOB_V6_NAT -s (\S+) -o ppp0 -j NETMAP --to (\S+)",
                             snapshot.nat_chain[1])
        if match and match[1] == str(config.ula):
            try:
                mapped = ipaddress.IPv6Network(match[2], strict=True)
            except ValueError:
                mapped = None
            if mapped is not None and mapped.is_global and mapped.prefixlen == 56:
                return "dsl", str(mapped)
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
    known_nat = snapshot.nat_chain in ((), expected_nat(config, "starlink", None))
    nat_mode = "starlink" if snapshot.nat_chain == expected_nat(config, "starlink", None) else None
    if not known_nat and len(snapshot.nat_chain) == 2 and snapshot.nat_chain[0] == "-N IOB_V6_NAT":
        match = re.fullmatch(r"-A IOB_V6_NAT -s (\S+) -o ppp0 -j NETMAP --to (\S+)", snapshot.nat_chain[1])
        if match and match[1] == str(config.ula):
            try:
                mapped = ipaddress.IPv6Network(match[2], strict=True)
                known_nat = mapped.is_global and mapped.prefixlen == 56
                nat_mode = "dsl" if known_nat else None
            except ValueError:
                pass
    if not known_nat:
        return False
    if snapshot.policy not in ((), expected_policy(config, "dsl"), expected_policy(config, "starlink")):
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
