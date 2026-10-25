"""Strict, minimal gateway observation protocol; raw UDAPI never reaches HA.

A forced-command helper must filter controller responses on the gateway. HA
accepts only this closed schema, not raw credentials, interface tables or logs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from ipaddress import IPv6Network
from typing import Any

from .failover import LinkHealth
from .network import NetworkDataError


@dataclass(frozen=True, slots=True)
class ProtocolObservation:
    """Protocol health and a confidential current delegation."""

    interface: str
    observed_at: datetime
    link_up: bool | None
    ipv4_probe: bool | None
    ipv6_probe: bool | None
    ipv6_default: bool | None
    ipv6_address: bool | None
    delegation_known: bool
    delegated_prefix: IPv6Network | None = field(repr=False)

    def ipv4_health(self) -> LinkHealth:
        """A confirmed carrier loss does not require a working probe route."""
        if self.link_up is False:
            return LinkHealth(None if self.ipv4_probe is True else False, self.observed_at)
        if self.link_up is None or self.ipv4_probe is None:
            return LinkHealth(None, self.observed_at)
        return LinkHealth(self.ipv4_probe, self.observed_at)

    def ipv6_health(self, *, require_delegation: bool) -> LinkHealth:
        # Probe setup can legitimately fail after the carrier disappears.
        # A successful probe at the same time contradicts that carrier result;
        # keep that case unknown rather than choosing a preferred observation.
        if self.link_up is False:
            return LinkHealth(None if self.ipv6_probe is True else False, self.observed_at)
        facts = (self.link_up, self.ipv6_probe, self.ipv6_default, self.ipv6_address)
        # Any missing required evidence blocks actuation, even alongside a
        # negative observation. Missing data is not a confirmed outage.
        if any(value is None for value in facts) or (
            require_delegation and not self.delegation_known
        ):
            return LinkHealth(None, self.observed_at)
        healthy = all(facts) and (not require_delegation or self.delegated_prefix is not None)
        return LinkHealth(healthy, self.observed_at)


@dataclass(frozen=True, slots=True)
class GatewayObservation:
    observed_at: datetime
    primary: ProtocolObservation
    secondary: ProtocolObservation


def _boolean(value: Any) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise NetworkDataError("Invalid gateway boolean")
    return value


def _timestamp(value: Any, now: datetime, maximum_age: timedelta) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NetworkDataError("Invalid gateway timestamp")
    try:
        observed = datetime.fromtimestamp(value / 1000, timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise NetworkDataError("Invalid gateway timestamp") from None
    if now.tzinfo is None or not timedelta(0) <= now - observed <= maximum_age:
        raise NetworkDataError("Stale or future gateway timestamp")
    return observed


def parse_gateway_observation(
    raw: Any,
    now: datetime,
    *,
    primary_interface: str,
    secondary_interface: str,
    maximum_age: timedelta = timedelta(seconds=20),
    delegated_prefix_length: int = 56,
) -> GatewayObservation:
    """Reject extra fields, ambiguous interfaces and unsafe prefixes."""
    if not isinstance(raw, Mapping) or set(raw) != {"schema", "observed_at_ms", "interfaces"}:
        raise NetworkDataError("Unexpected gateway observation schema")
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise NetworkDataError("Unsupported gateway observation version")
    if primary_interface == secondary_interface:
        raise NetworkDataError("WAN interfaces must be distinct")
    if not 1 <= delegated_prefix_length <= 64 or maximum_age <= timedelta(0):
        raise NetworkDataError("Invalid gateway observation policy")
    observed_at = _timestamp(raw["observed_at_ms"], now, maximum_age)
    rows = raw["interfaces"]
    if not isinstance(rows, list) or len(rows) != 2:
        raise NetworkDataError("Expected two gateway interface observations")
    parsed = {}
    fields = {
        "interface",
        "observed_at_ms",
        "link_up",
        "ipv4_probe",
        "ipv6_probe",
        "ipv6_default",
        "ipv6_address",
        "delegation_known",
        "delegated_prefix",
    }
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != fields:
            raise NetworkDataError("Unexpected interface observation schema")
        interface = row["interface"]
        if (
            not isinstance(interface, str)
            or interface not in (primary_interface, secondary_interface)
            or interface in parsed
        ):
            raise NetworkDataError("Ambiguous gateway interface")
        sample_at = _timestamp(row["observed_at_ms"], now, maximum_age)
        if sample_at > observed_at:
            raise NetworkDataError("Interface sample newer than its response")
        known = _boolean(row["delegation_known"])
        if known is None:
            raise NetworkDataError("Missing delegation knowledge")
        prefix = None
        if row["delegated_prefix"] is not None:
            if not known or not isinstance(row["delegated_prefix"], str):
                raise NetworkDataError("Conflicting delegation observation")
            try:
                prefix = IPv6Network(row["delegated_prefix"], strict=True)
            except ValueError:
                raise NetworkDataError("Invalid delegated prefix") from None
            if (
                not prefix.is_global
                or prefix.is_multicast
                or prefix.prefixlen != delegated_prefix_length
            ):
                raise NetworkDataError("Unsupported delegated prefix")
        parsed[interface] = ProtocolObservation(
            interface=interface,
            observed_at=sample_at,
            link_up=_boolean(row["link_up"]),
            ipv4_probe=_boolean(row["ipv4_probe"]),
            ipv6_probe=_boolean(row["ipv6_probe"]),
            ipv6_default=_boolean(row["ipv6_default"]),
            ipv6_address=_boolean(row["ipv6_address"]),
            delegation_known=known,
            delegated_prefix=prefix,
        )
    return GatewayObservation(observed_at, parsed[primary_interface], parsed[secondary_interface])
