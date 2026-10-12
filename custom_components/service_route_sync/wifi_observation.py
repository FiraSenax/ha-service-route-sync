"""Full legacy WiFi sampling contract from native UniFi client observations."""

from dataclasses import dataclass
from datetime import datetime
import math

from .client_observation import canonical_mac
from .network import finite_number


class WifiDataError(ValueError):
    """Sanitized invalid wireless snapshot."""


def text(value, maximum=128):
    return value if isinstance(value, str) and 0 < len(value) <= maximum else None


def integer(value, maximum):
    return value if type(value) is int and 0 <= value <= maximum else None


def dbm(value):
    if type(value) not in (int, float):
        return None
    try:
        return float(value) if math.isfinite(value) and -200 <= value <= 0 else None
    except OverflowError:
        return None


@dataclass(frozen=True, slots=True)
class WirelessObservation:
    ts: float
    mac: str
    name: str | None
    essid: str | None
    ap_mac: str | None
    radio: str | None
    channel: int | None
    width: int | None
    signal: float | None
    noise: float | None
    tx_rate: float | None
    rx_rate: float | None
    nss: int | None
    retry_pct: float | None
    tx_retries: float | None
    wifi_tx_attempts: float | None
    satisfaction: float | None


def observe_wireless(rows, observed_at: datetime):
    """A complete successful list can be empty; an invalid list cannot."""
    if (
        observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or not isinstance(rows, list)
        or len(rows) > 4096
    ):
        raise WifiDataError("Invalid wireless snapshot")
    now = observed_at.timestamp()
    identities, result = set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise WifiDataError("Invalid wireless client record")
        mac = canonical_mac(row.get("mac"))
        if mac is None or mac in identities:
            raise WifiDataError("Invalid or duplicate wireless identity")
        identities.add(mac)
        seen = finite_number(row.get("last_seen"))
        if row.get("is_wired") is not False or seen is None or not 0 <= now - seen < 120:
            continue
        result.append(
            WirelessObservation(
                now,
                mac,
                text(row.get("name")) or text(row.get("hostname")),
                text(row.get("essid")),
                canonical_mac(row.get("ap_mac")),
                text(row.get("radio"), 32),
                integer(row.get("channel"), 4096),
                integer(row.get("channel_width"), 320),
                dbm(row.get("signal")),
                dbm(row.get("noise")),
                finite_number(row.get("tx_rate")),
                finite_number(row.get("rx_rate")),
                integer(row.get("nss"), 16),
                finite_number(row.get("wifi_tx_retries_percentage"), maximum=100),
                finite_number(row.get("tx_retries")),
                finite_number(row.get("wifi_tx_attempts")),
                finite_number(row.get("satisfaction"), maximum=100),
            )
        )
    return tuple(sorted(result, key=lambda value: value.mac))
