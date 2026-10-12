"""Validated, router-independent WAN observations.

An API response is an observation, not a proof of end-to-end reachability.
Missing fields remain unknown; zero must never stand in for missing telemetry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Any


class NetworkDataError(ValueError):
    """The router did not provide a usable observation."""


def finite_number(value: Any, *, maximum: float | None = None) -> float | None:
    """Accept JSON numbers only, rejecting bool, strings and nonfinite values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number) or number < 0 or (maximum is not None and number > maximum):
        return None
    return number


@dataclass(frozen=True, slots=True)
class WanObservation:
    """A point-in-time WAN sample with independent optional measurements."""

    interface: str
    observed_at: datetime
    link_up: bool | None
    availability_pct: float | None
    latency_ms: float | None
    download_mbit_s: float | None
    upload_mbit_s: float | None

    def is_fresh(self, now: datetime, maximum_age: timedelta) -> bool:
        """Reject future, naive and stale samples, even after clock changes."""
        if now.tzinfo is None or self.observed_at.tzinfo is None:
            return False
        age = now - self.observed_at
        return timedelta(0) <= age <= maximum_age


def parse_wan(interface: str, raw: Any, observed_at: datetime) -> WanObservation:
    """Parse supported UniFi WAN telemetry without guessing current health.

    ``availability`` is a percentage over a measurement window. It is not
    current link state. Only an explicit boolean ``up`` is used as link state.
    Byte-per-second fields are converted to decimal Mbit/s exactly once.
    """
    if interface not in ("wan1", "wan2") or not isinstance(raw, Mapping):
        raise NetworkDataError("Missing or unsupported WAN telemetry")
    if observed_at.tzinfo is None:
        raise NetworkDataError("Observation timestamp must include a timezone")

    def rate(key: str) -> float | None:
        value = finite_number(raw.get(key), maximum=1_000_000_000_000)
        return value / 125_000 if value is not None else None

    return WanObservation(
        interface=interface,
        observed_at=observed_at,
        link_up=raw.get("up") if isinstance(raw.get("up"), bool) else None,
        availability_pct=finite_number(raw.get("availability"), maximum=100),
        latency_ms=finite_number(raw.get("latency")),
        download_mbit_s=rate("rx_bytes-r"),
        upload_mbit_s=rate("tx_bytes-r"),
    )


@dataclass(frozen=True, slots=True)
class NetworkObservation:
    """An immutable response from one deliberately selected gateway."""

    observed_at: datetime
    primary: WanObservation | None
    secondary: WanObservation | None


def parse_gateway(raw: Any, observed_at: datetime) -> NetworkObservation:
    """Allow a single-WAN gateway and partial telemetry without fake defaults."""
    if not isinstance(raw, Mapping):
        raise NetworkDataError("Unexpected gateway response")
    samples: dict[str, WanObservation | None] = {}
    for interface in ("wan1", "wan2"):
        value = raw.get(interface)
        samples[interface] = parse_wan(interface, value, observed_at) if value is not None else None
    if not any(samples.values()):
        raise NetworkDataError("The selected gateway exposes no supported WAN telemetry")
    return NetworkObservation(observed_at, samples["wan1"], samples["wan2"])
