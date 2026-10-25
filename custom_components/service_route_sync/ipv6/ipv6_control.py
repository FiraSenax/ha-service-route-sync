"""Pure, fail-closed policy for the gateway's bounded IPv6 actor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..backends.ipv6_actor_ssh import ActorStatus
from ..network.failover import FailoverPolicy, FailoverState, Wan, decide_failover
from ..network.gateway_observation import GatewayObservation


@dataclass(frozen=True, slots=True)
class IPv6Controls:
    enabled: bool
    dry_run: bool
    nat66: bool
    sab: bool
    override: str

    def __post_init__(self):
        if any(
            type(value) is not bool for value in (self.enabled, self.dry_run, self.nat66, self.sab)
        ):
            raise ValueError("IPv6 control must be explicit")
        if self.override not in ("auto", "primary", "secondary"):
            raise ValueError("Invalid IPv6 override")


@dataclass(frozen=True, slots=True)
class IPv6Plan:
    state: FailoverState
    target: str | None
    write: bool
    reason: str


def _wan(mode: str) -> Wan:
    return {"primary": Wan.PRIMARY, "secondary": Wan.SECONDARY}.get(mode, Wan.UNKNOWN)


def plan_ipv6(
    state: FailoverState,
    status: ActorStatus,
    observation: GatewayObservation,
    controls: IPv6Controls,
    now: datetime,
    *,
    local_latch: bool = False,
) -> IPv6Plan:
    """Only a fresh, exactly owned/blank state can produce a write proposal."""
    active = _wan(status.owned_mode)
    if active != state.active:
        state = FailoverState(active)
    if not controls.enabled:
        return IPv6Plan(FailoverState(active), None, False, "disabled")
    if not status.writable and not controls.dry_run:
        return IPv6Plan(FailoverState(active), None, False, "actor_read_only")
    if not controls.nat66 or not controls.sab:
        return IPv6Plan(FailoverState(active), None, False, "unsupported_feature_gates")
    if status.review_required or local_latch:
        return IPv6Plan(FailoverState(active), None, False, "review_required")
    if not status.repairable or (active is Wan.UNKNOWN and not status.blank):
        return IPv6Plan(FailoverState(), None, False, "unknown_router_state")

    primary = observation.primary.ipv6_health(require_delegation=True)
    secondary = observation.secondary.ipv6_health(require_delegation=False)
    maximum_age = timedelta(seconds=20)
    if any(
        link.healthy is None or not link.is_fresh(now, maximum_age) for link in (primary, secondary)
    ):
        return IPv6Plan(FailoverState(active), None, False, "unknown_health")

    preference = Wan.PRIMARY if controls.override in ("auto", "primary") else Wan.SECONDARY
    policy = FailoverPolicy(preference=preference)
    preferred, fallback = (
        (primary, secondary) if preference is Wan.PRIMARY else (secondary, primary)
    )
    target = (
        preference
        if preferred.healthy
        else (Wan.SECONDARY if preference is Wan.PRIMARY else Wan.PRIMARY)
        if fallback.healthy
        else Wan.DOWN
    )
    if target is Wan.DOWN:
        return IPv6Plan(FailoverState(active), None, False, "both_down")
    target_mode = "primary" if target is Wan.PRIMARY else "secondary"
    if not (status.primary_default if target is Wan.PRIMARY else status.secondary_default):
        return IPv6Plan(FailoverState(active), None, False, "target_route_unavailable")

    if active is Wan.UNKNOWN:
        # Only an exactly blank router may bootstrap. A restart never credits
        # observations made before this HA process began.
        observed_at = min(primary.observed_at, secondary.observed_at)
        continuous = (
            state.candidate is target
            and state.candidate_since is not None
            and state.last_observed_at is not None
            and timedelta(0) <= now - state.last_observed_at <= maximum_age
            and observed_at >= state.last_observed_at
        )
        candidate_since = state.candidate_since if continuous else now
        proposed = FailoverState(Wan.UNKNOWN, target, candidate_since, observed_at)
        if now - candidate_since < policy.failure_delay:
            return IPv6Plan(proposed, target_mode, False, "stabilizing")
        return IPv6Plan(
            proposed, target_mode, not controls.dry_run, "dry_run" if controls.dry_run else "ready"
        )

    result = decide_failover(
        state,
        primary,
        secondary,
        now,
        policy,
        enabled=True,
        dry_run=controls.dry_run,
        write_blocked=False,
    )
    if result.desired is Wan.DOWN:
        return IPv6Plan(result.state, None, False, "both_down")
    desired_mode = "primary" if result.desired is Wan.PRIMARY else "secondary"
    if result.desired is active and status.mode != status.owned_mode:
        # The actor recognized an older delegated prefix. Its current PD and
        # the independently measured DSL health are both authoritative.
        return IPv6Plan(
            result.state,
            desired_mode,
            not controls.dry_run,
            "dry_run_repair" if controls.dry_run else "repair_prefix",
        )
    return IPv6Plan(result.state, desired_mode, result.write_allowed, result.reason)
