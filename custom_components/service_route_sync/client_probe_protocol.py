"""Closed gateway client-probe protocol with no raw topology or credentials."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

from .client_metrics import ProbeObservation, RoutingObservation
from .network import NetworkDataError
from .network_metrics import MetricDataError


@dataclass(frozen=True, slots=True)
class ClientProbeSnapshot:
    observed_at: datetime
    probes: Mapping[str, ProbeObservation]
    routes: Mapping[str, RoutingObservation]
    identity_verified: Mapping[str, bool]


def timestamp(value, now):
    if type(value) is not int or now.tzinfo is None:
        raise NetworkDataError("Invalid client observation timestamp")
    try:
        stamp = datetime.fromtimestamp(value / 1000, timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise NetworkDataError("Invalid client observation timestamp") from None
    if not timedelta(0) <= now - stamp <= timedelta(seconds=75):
        raise NetworkDataError("Stale or future client observation")
    return stamp


def parse_client_probes(raw, now: datetime, profile_ids: tuple[str, ...]) -> ClientProbeSnapshot:
    if not isinstance(raw, dict) or set(raw) != {"schema", "observed_at_ms", "clients"}:
        raise NetworkDataError("Unexpected client observation schema")
    if type(raw["schema"]) is not int or raw["schema"] != 1:
        raise NetworkDataError("Unsupported client observation version")
    observed = timestamp(raw["observed_at_ms"], now)
    clients = raw["clients"]
    if (
        not 1 <= len(profile_ids) <= 16
        or len(set(profile_ids)) != len(profile_ids)
        or not isinstance(clients, dict)
        or set(clients) != set(profile_ids)
    ):
        raise NetworkDataError("Unexpected client profile identities")
    probes, routes, verified = {}, {}, {}
    for key, row in clients.items():
        if (
            not isinstance(row, dict)
            or set(row)
            != {"identity_verified", "probe_at_ms", "probe", "routing_at_ms", "connections"}
            or type(row["identity_verified"]) is not bool
        ):
            raise NetworkDataError("Unexpected client measurement schema")
        verified[key] = row["identity_verified"]
        if not verified[key] and any(
            row[field] is not None
            for field in ("probe_at_ms", "probe", "routing_at_ms", "connections")
        ):
            raise NetworkDataError("Unverified client supplied measurements")
        try:
            if row["probe"] is None:
                if row["probe_at_ms"] is not None:
                    raise NetworkDataError("Missing probe has a timestamp")
            else:
                probe = row["probe"]
                if not isinstance(probe, dict) or set(probe) != {
                    "loss",
                    "average_ms",
                    "maximum_ms",
                }:
                    raise NetworkDataError("Unexpected probe fields")
                stamp = timestamp(row["probe_at_ms"], now)
                if stamp > observed:
                    raise NetworkDataError("Probe postdates completed scan")
                if probe["loss"] is None or (
                    (probe["average_ms"] is None) != (probe["maximum_ms"] is None)
                ):
                    raise NetworkDataError("Incomplete probe measurement")
                value = ProbeObservation(
                    stamp, probe["loss"], probe["average_ms"], probe["maximum_ms"]
                )
                if value.loss < 100 and value.average_ms is None:
                    raise NetworkDataError("Successful probe lacks round-trip measurements")
                if value.average_ms is not None and value.average_ms > value.maximum_ms:
                    raise NetworkDataError("Inconsistent round-trip measurements")
                probes[key] = value
            if row["connections"] is None:
                if row["routing_at_ms"] is not None:
                    raise NetworkDataError("Missing routing has a timestamp")
            else:
                stamp = timestamp(row["routing_at_ms"], now)
                if (
                    stamp > observed
                    or not isinstance(row["connections"], dict)
                    or set(row["connections"]) != {"primary", "secondary", "vpn1", "vpn2", "other"}
                ):
                    raise NetworkDataError("Unexpected routing measurements")
                routes[key] = RoutingObservation(stamp, True, row["connections"])
        except MetricDataError:
            raise NetworkDataError("Invalid client numeric measurements") from None
    return ClientProbeSnapshot(
        observed, MappingProxyType(probes), MappingProxyType(routes), MappingProxyType(verified)
    )
