"""Validated client/port observations; no network calls or controller mutation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import ipaddress
import math
import re
from typing import Any

from .network import finite_number

_MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}\Z")


class ClientDataError(ValueError):
    """An observation or configured identity cannot be trusted."""


def canonical_mac(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    result = value.strip().lower()
    if not _MAC.fullmatch(result) or result == "00:00:00:00:00:00" or int(result[:2], 16) & 1:
        return None
    return result


@dataclass(frozen=True, slots=True)
class PortObservation:
    identity: str
    name: str
    up: bool | None
    speed_mbit_s: float | None
    rx_errors: float | None
    tx_errors: float | None
    rx_dropped: float | None
    tx_dropped: float | None
    rx_bytes: float | None
    tx_bytes: float | None
    link_down_count: float | None
    switch_uptime_s: float | None


@dataclass(frozen=True, slots=True)
class ClientObservation:
    observed_at: datetime
    online: bool
    connection: str
    ipv4_address: str | None
    port: PortObservation | None
    port_ambiguous: bool
    wifi: WifiObservation | None


@dataclass(frozen=True, slots=True)
class WifiObservation:
    signal: float | None
    noise: float | None
    tx_rate: float | None
    rx_rate: float | None
    tx_retries: float | None
    tx_packets: float | None


def _fresh(row: Mapping, now: float) -> bool:
    stamp = finite_number(row.get("last_seen"))
    return stamp is not None and 0 <= now - stamp < 120


def _radio_dbm(value: Any) -> float | None:
    """Preserve signed dBm without coercing strings or boolean flags."""
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and -200 <= number <= 0 else None


def observe_client(
    macs: Sequence[str], clients: Any, devices: Any, observed_at: datetime
) -> ClientObservation:
    """Match exact configured identities and preserve missing measurements.

    Client absence is offline only after a successfully obtained complete
    client list. Historical switch ports remain useful locations, never an
    active link for an offline or wireless client. Tied live port identities
    are explicitly ambiguous; input ordering must not select a random port.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ClientDataError("Observation time must have a timezone")
    if not isinstance(macs, Sequence) or isinstance(macs, (str, bytes)) or not macs:
        raise ClientDataError("At least one explicit client identity is required")
    identities = {canonical_mac(mac) for mac in macs}
    if None in identities:
        raise ClientDataError("Invalid configured client identity")
    if (
        not isinstance(clients, list)
        or not isinstance(devices, list)
        or any(not isinstance(row, Mapping) for row in [*clients, *devices])
    ):
        raise ClientDataError("Expected complete controller record lists")
    now = observed_at.timestamp()
    active = [
        row for row in clients if canonical_mac(row.get("mac")) in identities and _fresh(row, now)
    ]
    connection = "unknown"
    address = None
    wifi = None
    if active:
        selected = max(active, key=lambda row: row["last_seen"])
        wired = selected.get("is_wired")
        if isinstance(wired, bool):
            connection = "wired" if wired else "wifi"
        if connection == "wifi":
            wifi = WifiObservation(
                signal=_radio_dbm(selected.get("signal")),
                noise=_radio_dbm(selected.get("noise")),
                tx_rate=finite_number(selected.get("tx_rate")),
                rx_rate=finite_number(selected.get("rx_rate")),
                tx_retries=finite_number(selected.get("tx_retries")),
                tx_packets=finite_number(selected.get("tx_packets")),
            )
        try:
            raw_address = selected.get("ip")
            if isinstance(raw_address, str):
                address = str(ipaddress.IPv4Address(raw_address))
        except ipaddress.AddressValueError:
            pass
    candidates = []
    for device in devices:
        if not _fresh(device, now) or canonical_mac(device.get("mac")) is None:
            continue
        table = device.get("port_table")
        if not isinstance(table, list):
            continue
        for port in table:
            if not isinstance(port, Mapping) or not isinstance(
                port.get("last_connection"), Mapping
            ):
                continue
            last = port["last_connection"]
            if canonical_mac(last.get("mac")) not in identities:
                continue
            index = port.get("port_idx")
            if type(index) is not int or not 0 < index <= 65535:
                continue
            seen = finite_number(last.get("last_seen"))
            if seen is not None and seen > now:
                continue
            score = (port.get("up") is True and last.get("connected") is True, seen or 0)
            candidates.append((score, device, port))
    chosen = None
    ambiguous = False
    if candidates:
        score = max(row[0] for row in candidates)
        winners = [row for row in candidates if row[0] == score]
        identities_found = {(canonical_mac(row[1]["mac"]), row[2]["port_idx"]) for row in winners}
        ambiguous = len(identities_found) > 1
        if not ambiguous:
            _, device, port = winners[0]
            last = port["last_connection"]
            up = False
            if active and connection == "unknown":
                up = None
            if active and connection == "wired":
                up = (
                    (port["up"] and last["connected"])
                    if type(port.get("up")) is bool and type(last.get("connected")) is bool
                    else None
                )
            label = device.get("name")
            label = label[:128] if isinstance(label, str) and label else "Switch"
            chosen = PortObservation(
                identity=f"{canonical_mac(device['mac'])}:{port['port_idx']}",
                name=f"{label} Port {port['port_idx']}",
                up=up,
                speed_mbit_s=finite_number(port.get("speed")),
                rx_errors=finite_number(port.get("rx_errors")),
                tx_errors=finite_number(port.get("tx_errors")),
                rx_dropped=finite_number(port.get("rx_dropped")),
                tx_dropped=finite_number(port.get("tx_dropped")),
                rx_bytes=finite_number(port.get("rx_bytes")),
                tx_bytes=finite_number(port.get("tx_bytes")),
                link_down_count=finite_number(port.get("link_down_count")),
                switch_uptime_s=finite_number(device.get("uptime")),
            )
    return ClientObservation(
        observed_at, bool(active), connection, address, chosen, ambiguous, wifi
    )
