"""Validate persisted incident state without exposing private field contents."""

import math
import re

from .windows_queue import normalize_event


def number(value, nullable=False):
    if value is None and nullable:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2**53:
        raise ValueError("Invalid state number")


def text(value, limit=200):
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        raise ValueError("Invalid state text")


def validate_engine(engine):
    times = {
        "up_since",
        "down_since",
        "wake_replies_since",
        "power_off",
        "power_on",
        "switch_uptime",
    }
    allowed = times | {
        "incidents",
        "port_identity",
        "port",
        "expected_power_off",
        "last_pulse",
        "link_flap_window",
    }
    if not isinstance(engine, dict) or not set(engine) <= allowed:
        raise ValueError("Unexpected incident state fields")
    for key in times & engine.keys():
        number(engine[key], nullable=key == "switch_uptime")
    if "port_identity" in engine and engine["port_identity"] is not None:
        text(engine["port_identity"])
    if "expected_power_off" in engine and type(engine["expected_power_off"]) is not bool:
        raise ValueError("Invalid power flag")
    incidents = engine.get("incidents", {})
    if not isinstance(incidents, dict) or len(incidents) > 128:
        raise ValueError("Invalid incident map")
    for key, row in incidents.items():
        text(key)
        if (
            not isinstance(row, dict)
            or not {"active", "since", "good"} <= set(row)
            or not set(row) <= {"active", "since", "good", "last_sent"}
            or type(row["active"]) is not bool
        ):
            raise ValueError("Invalid incident record")
        for field in set(row) - {"active"}:
            number(row[field], nullable=field in {"since", "good"})
    pulses = engine.get("last_pulse", {})
    if not isinstance(pulses, dict) or len(pulses) > 128:
        raise ValueError("Invalid pulse map")
    for key, value in pulses.items():
        text(key)
        number(value)
    port = engine.get("port")
    if port is not None:
        fields = {
            "up",
            "speed",
            "link_down_count",
            "rx_errors",
            "tx_errors",
            "rx_dropped",
            "tx_dropped",
            "rx_bytes",
            "tx_bytes",
        }
        if not isinstance(port, dict) or not set(port) <= fields:
            raise ValueError("Invalid port snapshot")
        for key, value in port.items():
            if key == "up":
                if value is not None and type(value) is not bool:
                    raise ValueError("Invalid link flag")
            else:
                number(value, nullable=True)
    window = engine.get("link_flap_window")
    if window is not None:
        if not isinstance(window, dict) or set(window) != {
            "port",
            "count",
            "uptime",
            "time",
            "deltas",
            "last_change",
        }:
            raise ValueError("Invalid link window")
        text(window["port"])
        for key in ("count", "uptime", "time", "last_change"):
            number(window[key])
        if not isinstance(window["deltas"], list) or len(window["deltas"]) > 1000:
            raise ValueError("Invalid link window deltas")
        for row in window["deltas"]:
            if not isinstance(row, list) or len(row) != 2:
                raise ValueError("Invalid link delta")
            for value in row:
                number(value)


def validate_queues(outbox, replay):
    identities = set()
    for row in outbox:
        if (
            set(row) != {"delivery_id", "event"}
            or not isinstance(row["delivery_id"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["delivery_id"])
        ):
            raise ValueError("Invalid notification identity")
        if row["delivery_id"] in identities:
            raise ValueError("Duplicate notification identity")
        identities.add(row["delivery_id"])
        event = row["event"]
        if not isinstance(event, dict) or set(event) != {"key", "message", "time", "recovery"}:
            raise ValueError("Invalid notification record")
        text(event["key"])
        text(event["message"], 8192)
        number(event["time"])
        if type(event["recovery"]) is not bool:
            raise ValueError("Invalid notification recovery flag")
    identities = set()
    for row in replay:
        if set(row) != {"event", "delivered"} or type(row["delivered"]) is not bool:
            raise ValueError("Invalid replay record")
        _, _, identity = normalize_event(row["event"])
        if identity in identities:
            raise ValueError("Duplicate replay identity")
        identities.add(identity)
