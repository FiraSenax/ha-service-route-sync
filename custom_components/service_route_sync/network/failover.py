"""Pure failover decisions, independent of Home Assistant and router commands.

The planner never executes changes. Unknown or stale observations block writes.
Hysteresis uses elapsed time, not poll counts, so retries and restarts cannot
silently shorten the configured delay.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum


class Wan(StrEnum):
    """Explicit WAN identities; no default chosen on missing persisted state."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    DOWN = "down"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LinkHealth:
    """Verified protocol-specific health, not rolling availability percent."""

    healthy: bool | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.healthy is not None and not isinstance(self.healthy, bool):
            raise ValueError("Health must be an explicit boolean or unknown")

    def is_fresh(self, now: datetime, maximum_age: timedelta) -> bool:
        if self.observed_at.tzinfo is None or now.tzinfo is None:
            return False
        return timedelta(0) <= now - self.observed_at <= maximum_age


@dataclass(frozen=True, slots=True)
class FailoverPolicy:
    """Timing and explicit operator preference."""

    failure_delay: timedelta = timedelta(seconds=15)
    recovery_delay: timedelta = timedelta(seconds=30)
    maximum_age: timedelta = timedelta(seconds=20)
    preference: Wan = Wan.PRIMARY

    def __post_init__(self) -> None:
        if (
            self.failure_delay < timedelta(0)
            or self.recovery_delay < timedelta(0)
            or self.maximum_age <= timedelta(0)
            or self.preference not in (Wan.PRIMARY, Wan.SECONDARY)
        ):
            raise ValueError("Invalid failover policy")


@dataclass(frozen=True, slots=True)
class FailoverState:
    """Persisted observations; active WAN changes only after adapter verification."""

    active: Wan = Wan.UNKNOWN
    candidate: Wan = Wan.UNKNOWN
    candidate_since: datetime | None = None
    last_observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FailoverDecision:
    """A proposed change or a reason to hold the verified current state."""

    state: FailoverState
    desired: Wan
    write_allowed: bool
    reason: str


def decide_failover(
    state: FailoverState,
    primary: LinkHealth,
    secondary: LinkHealth,
    now: datetime,
    policy: FailoverPolicy,
    *,
    enabled: bool,
    dry_run: bool,
    write_blocked: bool,
) -> FailoverDecision:
    """Return a deterministic proposal; the caller owns durable write intent."""
    if now.tzinfo is None:
        raise ValueError("Decision timestamp must include a timezone")
    # Unknown periods break continuity and reset the hysteresis clock.
    if not enabled:
        return FailoverDecision(FailoverState(state.active), state.active, False, "disabled")
    if state.active == Wan.UNKNOWN:
        return FailoverDecision(FailoverState(), Wan.UNKNOWN, False, "unknown_router_state")
    if any(
        health.healthy is None or not health.is_fresh(now, policy.maximum_age)
        for health in (primary, secondary)
    ):
        return FailoverDecision(FailoverState(state.active), state.active, False, "unknown_health")

    preferred, fallback = (
        (primary, secondary) if policy.preference == Wan.PRIMARY else (secondary, primary)
    )
    opposite = Wan.SECONDARY if policy.preference == Wan.PRIMARY else Wan.PRIMARY
    desired = policy.preference if preferred.healthy else opposite if fallback.healthy else Wan.DOWN
    if desired == state.active:
        return FailoverDecision(FailoverState(state.active), desired, False, "current")

    candidate_since = state.candidate_since
    if (
        state.candidate != desired
        or candidate_since is None
        or candidate_since.tzinfo is None
        or candidate_since > now
        or state.last_observed_at is None
        or state.last_observed_at.tzinfo is None
        or not timedelta(0) <= now - state.last_observed_at <= policy.maximum_age
        or min(primary.observed_at, secondary.observed_at) < state.last_observed_at
    ):
        candidate_since = now
    proposed = replace(
        state,
        candidate=desired,
        candidate_since=candidate_since,
        last_observed_at=min(primary.observed_at, secondary.observed_at),
    )
    delay = policy.recovery_delay if desired == policy.preference else policy.failure_delay
    if now - candidate_since < delay:
        return FailoverDecision(proposed, desired, False, "stabilizing")
    if write_blocked:
        return FailoverDecision(proposed, desired, False, "review_required")
    return FailoverDecision(proposed, desired, not dry_run, "dry_run" if dry_run else "ready")
