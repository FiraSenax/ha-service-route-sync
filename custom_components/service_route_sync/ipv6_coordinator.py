"""HA-owned IPv6 failover with durable write intent and an uncertain-write latch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
import re
from uuid import uuid4

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .backends.gateway_ssh import GatewayTransportError
from .backends.ipv6_actor_ssh import (
    ActorStatus,
    IPv6ActorSSHConfig,
    apply_actor,
    inspect_actor,
)
from .failover import FailoverState
from .gateway_coordinator import GatewayCoordinator
from .gateway_credentials import credential_directory
from .ipv6_control import IPv6Controls, IPv6Plan, plan_ipv6
from .runtime import RouteConfigEntry

_LOGGER = logging.getLogger(__name__)
_DEFAULT_ENTITIES = {
    "enabled": "input_boolean.ipv6_failover_aktiv",
    "dry_run": "input_boolean.ipv6_failover_dry_run",
    "nat66": "input_boolean.ipv6_failover_nat66_aktiv",
    "sab": "input_boolean.ipv6_failover_sab_routing_aktiv",
    "override": "input_select.ipv6_failover_override",
}


@dataclass(frozen=True, slots=True)
class IPv6RuntimeStatus:
    observed_at: datetime
    actor: ActorStatus
    plan: IPv6Plan
    preview: IPv6Plan
    local_latch: bool
    active_writer: bool


def reconciliation_allowed(saved: dict, actor: ActorStatus, digest: str, mode: str) -> bool:
    """Require an exact administrator readback after an uncertain transaction."""
    return (
        isinstance(saved, dict)
        and saved.get("phase") == "intent"
        and saved.get("target") in ("dsl", "starlink")
        and actor.writable
        and not actor.review_required
        and actor.healthy
        and actor.digest == digest
        and actor.mode == mode
        and (digest == saved.get("digest") or mode == saved["target"])
    )


class IPv6Coordinator(DataUpdateCoordinator[IPv6RuntimeStatus]):
    """Only this coordinator holds the dedicated actor key and write policy."""

    def __init__(
        self, hass: HomeAssistant, entry: RouteConfigEntry, gateway: GatewayCoordinator
    ) -> None:
        self.entry = entry
        self.gateway = gateway
        self.settings = {**entry.data, **entry.options}
        self.transport = IPv6ActorSSHConfig(
            self.settings["ipv6_actor_host"],
            credential_directory(hass, self.settings["ipv6_actor_key_id"]),
        )
        self.store = Store(
            hass,
            1,
            f"service_route_sync.ipv6.{entry.entry_id}",
            private=True,
            atomic_writes=True,
            serialize_in_event_loop=False,
        )
        self.failover_state = FailoverState()
        self.shadow_state = FailoverState()
        self.local_latch = True  # No write before durable state has been inspected.
        self.active_writer = self.settings.get("ipv6_actor_active") is True
        self._initialized = False
        super().__init__(
            hass,
            _LOGGER,
            name="Service Route Sync IPv6 policy",
            config_entry=entry,
            update_interval=timedelta(seconds=10),
        )

    async def async_initialize(self) -> None:
        try:
            saved = await self.store.async_load()
        except (OSError, ValueError):
            raise UpdateFailed("IPv6 policy state is unreadable") from None
        if saved is None:
            self.local_latch = False
        elif (
            isinstance(saved, dict)
            and set(saved)
            in (
                {"phase", "transaction_id", "digest"},
                {"phase", "transaction_id", "digest", "target"},
            )
            and saved["phase"] in ("intent", "complete")
            and isinstance(saved["transaction_id"], str)
            and re.fullmatch(r"[0-9a-f]{32}", saved["transaction_id"])
            and isinstance(saved["digest"], str)
            and re.fullmatch(r"[0-9a-f]{64}", saved["digest"])
            and ("target" not in saved or saved["target"] in ("dsl", "starlink"))
        ):
            self.local_latch = saved["phase"] == "intent"
        else:
            raise UpdateFailed("IPv6 policy state requires review")
        self._initialized = True

    def _controls(self) -> IPv6Controls:
        values = {}
        for name, default in _DEFAULT_ENTITIES.items():
            entity_id = self.settings.get(f"ipv6_{name}_entity", default)
            state = self.hass.states.get(entity_id)
            if state is None:
                raise UpdateFailed("IPv6 control entity is unavailable")
            if name == "override":
                values[name] = state.state
            elif state.state in ("on", "off"):
                values[name] = state.state == "on"
            else:
                raise UpdateFailed("IPv6 control entity is unavailable")
        try:
            return IPv6Controls(**values)
        except ValueError:
            raise UpdateFailed("IPv6 control entity has an invalid value") from None

    async def _durable_save(self, value: dict) -> None:
        if self.hass.state is CoreState.stopping:
            raise UpdateFailed("HA is stopping")
        task = asyncio.create_task(self.store.async_save(value))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _async_update_data(self) -> IPv6RuntimeStatus:
        if not self._initialized:
            raise UpdateFailed("IPv6 policy state has not been loaded")
        try:
            await self.hass.async_add_executor_job(self.transport.validate_files)
            actor = await inspect_actor(self.transport)
            if self.active_writer and not actor.writable:
                raise UpdateFailed("Gateway IPv6 actor is read-only")
            observation = self.gateway.data
            if observation is None or not self.gateway.last_update_success:
                raise UpdateFailed("Verified WAN observation is unavailable")
            controls = self._controls()
            now = dt_util.utcnow()
            preview = plan_ipv6(
                self.shadow_state,
                actor,
                observation,
                replace(controls, dry_run=True),
                now,
                local_latch=self.local_latch,
            )
            self.shadow_state = preview.state
            operational = replace(controls, enabled=controls.enabled and self.active_writer)
            plan = plan_ipv6(
                self.failover_state,
                actor,
                observation,
                operational,
                now,
                local_latch=self.local_latch,
            )
            self.failover_state = plan.state
            if plan.write and plan.target:
                transaction_id = uuid4().hex
                await self._durable_save(
                    {
                        "phase": "intent",
                        "transaction_id": transaction_id,
                        "digest": actor.digest,
                        "target": plan.target,
                    }
                )
                self.local_latch = True
                result = await apply_actor(
                    self.transport,
                    transaction_id=transaction_id,
                    expected_digest=actor.digest,
                    mode=plan.target,
                )
                checked = await inspect_actor(self.transport)
                if (
                    checked.mode != plan.target
                    or checked.digest != result.digest
                    or checked.review_required
                ):
                    raise UpdateFailed("IPv6 policy readback requires review")
                await self._durable_save(
                    {
                        "phase": "complete",
                        "transaction_id": transaction_id,
                        "digest": checked.digest,
                        "target": plan.target,
                    }
                )
                self.local_latch = False
                actor = checked
                # A switch is accepted only after the actor's independent readback.
                self.failover_state = FailoverState()
                plan = plan_ipv6(
                    self.failover_state, actor, observation, operational, dt_util.utcnow()
                )
            return IPv6RuntimeStatus(
                dt_util.utcnow(), actor, plan, preview, self.local_latch, self.active_writer
            )
        except UpdateFailed:
            raise
        except (OSError, GatewayTransportError, ValueError, TimeoutError):
            raise UpdateFailed("Verified IPv6 gateway policy is unavailable") from None

    async def async_acknowledge_review(self, digest: str, mode: str) -> None:
        """Admin-only recovery after a separate gateway journal/rule audit."""
        if (
            self.active_writer
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or mode not in ("dsl", "starlink")
        ):
            raise ValueError("IPv6 writer must be inactive and readback exact")
        saved = await self.store.async_load()
        actor = await inspect_actor(self.transport)
        if not reconciliation_allowed(saved, actor, digest, mode):
            raise ValueError("IPv6 policy state requires manual gateway review")
        await self._durable_save(
            {
                "phase": "complete",
                "transaction_id": saved["transaction_id"],
                "digest": digest,
                "target": mode,
            }
        )
        self.local_latch = False
        await self.async_request_refresh()
