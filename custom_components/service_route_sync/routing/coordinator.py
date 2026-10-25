"""Stage all feeds before writes; retain local recovery snapshots."""

import asyncio
from dataclasses import asdict
from datetime import timedelta
import logging

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ..backends.unifi import UniFiBackend
from ..const import DOMAIN
from .core import SyncError, apply_plan, build_plans, restore_backup
from .providers import FeedClient

_LOGGER = logging.getLogger(__name__)


class RouteCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        self.entry = entry
        self.settings = {**entry.data, **entry.options}
        self.backend = UniFiBackend(hass, self.settings)
        self.feeds = FeedClient(async_get_clientsession(hass), self.settings)
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.backups")
        self.backups = []
        self.write_blocked = False
        self.last_success = None
        self.lock = asyncio.Lock()
        self.normal_interval = timedelta(hours=self.settings.get("interval_hours", 24))
        super().__init__(
            hass,
            _LOGGER,
            name="Service Route Sync",
            config_entry=entry,
            update_interval=self.normal_interval,
        )

    async def load(self):
        saved = await self.store.async_load() or {}
        self.backups = saved.get("backups", [])
        self.write_blocked = saved.get("write_blocked", False)
        if saved.get("last_success"):
            self.last_success = dt_util.parse_datetime(saved["last_success"])

    async def persist(self):
        await self.store.async_save(
            {
                "backups": self.backups,
                "write_blocked": self.write_blocked,
                "last_success": self.last_success.isoformat() if self.last_success else None,
            }
        )

    async def save_backup(self, plan):
        self.backups.append({**asdict(plan), "created": dt_util.utcnow().isoformat()})
        self.backups = self.backups[-20:]
        await self.persist()

    async def _async_update_data(self):
        async with self.lock:
            try:
                async with asyncio.timeout(30):
                    prefixes = await self.feeds.all_prefixes()
                routes = await self.backend.read_routes()
                plans = build_plans(routes, prefixes, self.settings["bindings"])
                changed = [plan for plan in plans if plan.changed]
                active = self.settings.get("automatic_apply", False)
                if active and (
                    self.backend.tls_pin_sha256 is None or self.backend.route_api_key is None
                ):
                    raise SyncError(
                        "Verified UniFi TLS trust and route API key are required for route writes"
                    )
                if active and changed and not self.write_blocked:
                    # Revalidate all removal limits before the first write.
                    build_plans(
                        routes,
                        prefixes,
                        self.settings["bindings"],
                        maximum_removal=self.settings.get("maximum_removal", 50) / 100,
                    )
                    # Persist a write latch BEFORE touching the router. A crash,
                    # uncertain response or partial cycle requires human review.
                    self.write_blocked = True
                    await self.persist()
                    for plan in changed:
                        await apply_plan(self.backend, plan, self.save_backup)
                    self.last_success = dt_util.utcnow()
                    self.write_blocked = False
                    await self.persist()
                    changed = []
                self.update_interval = self.normal_interval
                return {
                    "status": "review_required"
                    if self.write_blocked
                    else "current"
                    if active
                    else "preview",
                    "prefix_count": sum(len(values) for values in prefixes.values()),
                    "pending_routes": len(changed),
                    "last_success": self.last_success,
                    "services": {
                        plan.service: {
                            "prefixes": len(prefixes[plan.service]),
                            "added": plan.added,
                            "removed": plan.removed,
                            "removed_coverage_pct": round(plan.removed_coverage_pct, 3),
                            "added_coverage_pct": round(plan.added_coverage_pct, 3),
                        }
                        for plan in plans
                    },
                }
            except Exception as exc:
                _LOGGER.warning(
                    "Route cycle halted (%s%s)",
                    type(exc).__name__,
                    f": {exc}" if isinstance(exc, SyncError) else "",
                )
                # Keep snapshots and any uncertain-write latch; never retry writes.
                # A failed read must recover sooner than the normal daily poll,
                # including when only the WAN feature could initialize at boot.
                self.update_interval = timedelta(minutes=1)
                raise UpdateFailed(f"Service route sync stopped ({type(exc).__name__})") from None

    async def set_automatic(self, enabled):
        async with self.lock:
            if enabled and (
                self.backend.tls_pin_sha256 is None or self.backend.route_api_key is None
            ):
                raise SyncError(
                    "Configure verified UniFi TLS trust and route API key before route writes"
                )
            self.settings["automatic_apply"] = enabled
            if enabled:
                self.write_blocked = False  # Explicit user acknowledgement, not an automatic retry.
                await self.persist()
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, "automatic_apply": enabled}
            )
        # The entry update listener reloads the coordinator and requests a fresh plan.

    async def async_shutdown(self):
        await super().async_shutdown()
        await self.backend.async_close()

    async def restore_last(self):
        async with self.lock:
            if self.settings.get("automatic_apply", False):
                raise SyncError("Turn off automatic route updates before restoring")
            if not self.backups:
                raise SyncError("No local backup is available")
            backup = self.backups[-1]
            await restore_backup(self.backend, backup)
            self.backups.pop()
            self.write_blocked = False
            await self.persist()
        await self.async_request_refresh()
