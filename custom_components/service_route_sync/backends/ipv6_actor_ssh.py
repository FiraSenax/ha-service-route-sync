"""Dedicated, pinned SSH profile for the closed IPv6 gateway actor."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from .gateway_ssh import GatewaySSHConfig, GatewayTransportError, exchange_pinned_command

_COMMAND = "service-route-sync ipv6 v1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TRANSACTION = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True, slots=True)
class IPv6ActorSSHConfig(GatewaySSHConfig):
    """A separate key cannot be reused for the read-only observer."""

    @property
    def private_key(self):
        return self.credential_directory / "ipv6_actor_ed25519"

    def arguments(self) -> tuple[str, ...]:
        base = GatewaySSHConfig.arguments(self)
        return (*base[:-1], _COMMAND)


@dataclass(frozen=True, slots=True)
class ActorStatus:
    mode: str
    writable: bool
    owned_mode: str
    repairable: bool
    blank: bool
    healthy: bool
    digest: str
    delegation_ready: bool
    primary_default: bool
    secondary_default: bool
    review_required: bool


@dataclass(frozen=True, slots=True)
class ActorResult:
    mode: str
    applied: bool
    digest: str
    review_required: bool


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise GatewayTransportError("Invalid gateway state digest")
    return value


def _mode(value: Any) -> str:
    if value not in ("primary", "secondary", "unknown") or not isinstance(value, str):
        raise GatewayTransportError("Invalid gateway policy mode")
    return value


def _bool(value: Any) -> bool:
    if type(value) is not bool:
        raise GatewayTransportError("Invalid gateway policy status")
    return value


def parse_actor_response(raw: Any, operation: str) -> ActorStatus | ActorResult:
    if not isinstance(raw, dict) or type(raw.get("schema")) is not int or raw["schema"] != 1:
        raise GatewayTransportError("Invalid gateway actor response")
    if operation == "inspect":
        fields = {
            "schema",
            "operation",
            "mode",
            "writable",
            "owned_mode",
            "repairable",
            "blank",
            "healthy",
            "digest",
            "delegation_ready",
            "primary_default",
            "secondary_default",
            "review_required",
        }
        if set(raw) != fields or raw["operation"] != "inspect":
            raise GatewayTransportError("Invalid gateway actor response")
        mode = _mode(raw["mode"])
        owned_mode = _mode(raw["owned_mode"])
        repairable, blank = _bool(raw["repairable"]), _bool(raw["blank"])
        healthy = _bool(raw["healthy"])
        if (
            healthy != (mode in ("primary", "secondary"))
            or (mode != "unknown" and (mode != owned_mode or not repairable or blank))
            or (blank and (mode != "unknown" or owned_mode != "unknown" or not repairable))
            or (owned_mode != "unknown" and not repairable)
        ):
            raise GatewayTransportError("Conflicting gateway policy status")
        return ActorStatus(
            mode,
            _bool(raw["writable"]),
            owned_mode,
            repairable,
            blank,
            healthy,
            _digest(raw["digest"]),
            _bool(raw["delegation_ready"]),
            _bool(raw["primary_default"]),
            _bool(raw["secondary_default"]),
            _bool(raw["review_required"]),
        )
    if operation == "apply":
        fields = {"schema", "operation", "mode", "applied", "digest", "review_required"}
        if set(raw) != fields or raw["operation"] != "apply":
            raise GatewayTransportError("Invalid gateway actor response")
        mode = _mode(raw["mode"])
        if mode == "unknown" or _bool(raw["review_required"]):
            raise GatewayTransportError("Invalid gateway write result")
        return ActorResult(mode, _bool(raw["applied"]), _digest(raw["digest"]), False)
    raise GatewayTransportError("Invalid gateway actor operation")


async def inspect_actor(config: IPv6ActorSSHConfig) -> ActorStatus:
    payload = b'{"schema":1,"operation":"inspect"}'
    raw = await exchange_pinned_command(config, input_data=payload)
    return parse_actor_response(raw, "inspect")


async def apply_actor(
    config: IPv6ActorSSHConfig, *, transaction_id: str, expected_digest: str, mode: str
) -> ActorResult:
    if not isinstance(transaction_id, str) or not _TRANSACTION.fullmatch(transaction_id):
        raise GatewayTransportError("Invalid gateway transaction")
    _digest(expected_digest)
    if mode not in ("primary", "secondary") or not isinstance(mode, str):
        raise GatewayTransportError("Invalid gateway policy mode")
    payload = json.dumps(
        {
            "schema": 1,
            "operation": "apply",
            "transaction_id": transaction_id,
            "expected_digest": expected_digest,
            "mode": mode,
        },
        separators=(",", ":"),
    ).encode("ascii")
    # Gateway verifies each rule edit and may need a full bounded rollback.
    # Keep the SSH deadline longer than that transaction; an uncertain result
    # still latches both sides and is never retried automatically.
    raw = await exchange_pinned_command(config, input_data=payload, timeout=60)
    return parse_actor_response(raw, "apply")
