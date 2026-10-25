"""Strict fixed DSL observation protocol and native incident source adapter."""

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
import math


class DSLDataError(ValueError):
    """Sanitized DSL protocol failure."""


@dataclass(frozen=True)
class DSLObservation:
    observed_at: datetime
    probes: object


def measurement(value, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise DSLDataError("Invalid DSL measurement")


def parse_dsl(raw, now):
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema", "observed_at_ms", "probes"}
        or type(raw["schema"]) is not int
        or raw["schema"] != 1
    ):
        raise DSLDataError("Invalid DSL observation schema")
    if type(raw["observed_at_ms"]) is not int:
        raise DSLDataError("Invalid DSL timestamp")
    try:
        observed = datetime.fromtimestamp(raw["observed_at_ms"] / 1000, timezone.utc)
        age = (now - observed).total_seconds()
    except (TypeError, ValueError, OverflowError, OSError):
        raise DSLDataError("Invalid DSL observation clock") from None
    if not 0 <= age <= 20:
        raise DSLDataError("Stale or future DSL observation")
    probes = raw["probes"]
    if not isinstance(probes, dict) or set(probes) != {"wan_cf", "wan_google"}:
        raise DSLDataError("Invalid DSL observation targets")
    result = {}
    errors = {"dsl_route_unavailable", "dsl_route_changed", "invalid_probe", "probe_unavailable"}
    for key, row in probes.items():
        if (
            not isinstance(row, dict)
            or set(row) != {"loss", "avg", "max", "rc", "error"}
            or type(row["rc"]) is not int
            or row["rc"] not in (0, 1, 2)
        ):
            raise DSLDataError("Invalid DSL result fields")
        if row["error"] is not None:
            if (
                not isinstance(row["error"], str)
                or row["error"] not in errors
                or row["rc"] != 2
                or any(row[field] is not None for field in ("loss", "avg", "max"))
            ):
                raise DSLDataError("Inconsistent DSL source failure")
        else:
            measurement(row["loss"], 100)
            if row["rc"] == 2 or (row["rc"] == 1) != (row["loss"] == 100):
                raise DSLDataError("Inconsistent DSL probe result")
            if row["loss"] == 100:
                if row["avg"] is not None or row["max"] is not None:
                    raise DSLDataError("Loss-only DSL probe supplied RTT")
            else:
                measurement(row["avg"], 60000)
                measurement(row["max"], 60000)
                if row["avg"] > row["max"]:
                    raise DSLDataError("Inconsistent DSL round-trip times")
        result[key] = MappingProxyType(dict(row))
    return DSLObservation(observed, MappingProxyType(result))


class MonitoringDSLReader:
    def __init__(self, config):
        self.config = config

    async def __call__(self):
        from homeassistant.util import dt as dt_util
        from .backends.gateway_ssh import read_gateway
        from .monitoring_coordinator import TimedObservation

        raw = await read_gateway(self.config, timeout=14)
        observation = parse_dsl(raw, dt_util.utcnow())
        return TimedObservation(
            {key: dict(row) for key, row in observation.probes.items()},
            observation.observed_at.timestamp(),
        )
