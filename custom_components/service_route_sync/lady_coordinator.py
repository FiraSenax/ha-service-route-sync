"""Independent incident lifecycle with source freshness and durable delivery."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
import math
import copy

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .lady_store import LadyCheckpointError
from .windows_cursor import WindowsSourceError
from .windows_protocol import parse_cursor
from .windows_source import WindowsBatch
from .backends.gateway_ssh import GatewayTransportError
from .client_observation import ClientDataError
from .lady_dsl import DSLDataError
from .lady_engine import pc_monitoring_status

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimedObservation:
    value: dict
    observed_at: float


class LadyCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, transaction, notifier, readers, *, publish_heartbeat=True):
        if set(readers) != {"api", "probe", "dns", "windows"}:
            raise ValueError("Lady requires all configured observation sources")
        self.transaction = transaction
        self.notifier = notifier
        self.readers = dict(readers)
        self._source_failures = {}
        self._windows_cache = None
        self._windows_observed_at = None
        self._windows_next_poll = 0.0
        self._listener = None
        self.publish_heartbeat = publish_heartbeat
        self.history_snapshot = None
        self.metrics_snapshot = None
        super().__init__(
            hass,
            _LOGGER,
            name="Lady network incidents",
            config_entry=entry,
            update_interval=timedelta(seconds=5),
        )

    async def async_start(self):
        # Keep polling even with no subscribed entities or open dashboards.
        if self._listener is None:
            self._listener = self.async_add_listener(lambda: None)
        await self.async_refresh()

    async def async_stop(self):
        if self._listener is not None:
            self._listener()
            self._listener = None

    async def _read(self, name, cursor):
        try:
            async with asyncio.timeout(22 if name == "probe" else 15):
                value = (
                    await self.readers[name](cursor)
                    if name == "windows"
                    else await self.readers[name]()
                )
                if name in self._source_failures:
                    _LOGGER.info("Lady source %s recovered", name)
                    del self._source_failures[name]
                return value
        except (
            HomeAssistantError,
            GatewayTransportError,
            WindowsSourceError,
            ClientDataError,
            DSLDataError,
            OSError,
            TimeoutError,
        ) as error:
            failure = (
                str(error) if isinstance(error, GatewayTransportError) else type(error).__name__
            )
            if self._source_failures.get(name) != failure:
                _LOGGER.warning("Lady source %s unavailable (%s)", name, failure)
                self._source_failures[name] = failure
            return None

    async def _read_windows(self, cursor, now):
        # A new SSH session every five seconds is classified as a scan by the
        # gateway IPS. Reuse only the heartbeat until the next archive page;
        # replaying cached events would duplicate Windows incidents.
        cached = self._windows_cache
        if cached is not None and cursor == cached.cursor and now < self._windows_next_poll:
            return WindowsBatch(
                cached.cursor, (), cached.agent, cached.heartbeat_ok, False, 0, False
            )
        if cached is None and now < self._windows_next_poll:
            return None
        self._windows_next_poll = now + 60
        batch = await self._read("windows", cursor)
        self._windows_cache = batch if isinstance(batch, WindowsBatch) else None
        self._windows_observed_at = dt_util.utcnow().timestamp() if self._windows_cache else None
        return batch

    async def _async_update_data(self):
        state = self.transaction.store.state
        stored = state["cursor"] if state is not None else None
        cursor = parse_cursor(stored, schema=2 if stored and "day" in stored else 1, nullable=True)
        names = ("api", "probe", "dns", "windows")
        started = dt_util.utcnow().timestamp()
        values = await asyncio.gather(
            *(self._read(name, cursor) for name in names[:3]), self._read_windows(cursor, started)
        )
        now = dt_util.utcnow().timestamp()
        row, health = {}, {}
        for name, value in zip(names[:3], values[:3], strict=True):
            age_bound = 100 if name == "dns" else 45
            fresh = (
                isinstance(value, TimedObservation)
                and isinstance(value.value, dict)
                and type(value.observed_at) in (int, float)
                and math.isfinite(value.observed_at)
                and 0 <= now - value.observed_at <= age_bound
            )
            health[name] = fresh
            if name in ("api", "probe"):
                row[name + "_ok"] = fresh
            if fresh:
                if name == "api":
                    allowed = {
                        "port",
                        "port_identity",
                        "tracked_port",
                        "switch_uptime",
                        "planned_link_test",
                    }
                    row.update({key: val for key, val in value.value.items() if key in allowed})
                else:
                    row["probes" if name == "probe" else "dns"] = value.value
        batch = values[3]
        if not isinstance(batch, WindowsBatch):
            batch = WindowsBatch(None, (), None, False, False, 0, False)
        health["windows"] = (
            batch.heartbeat_ok
            and self._windows_observed_at is not None
            and 0 <= now - self._windows_observed_at <= 75
        )
        try:
            result = await self.transaction.async_sample(row, batch, now)
        except (LadyCheckpointError, WindowsSourceError, OSError, ValueError):
            raise UpdateFailed("Lady observation checkpoint could not be committed") from None
        pc_observation = {**row, "windows": {"agent": batch.agent}}
        pc_status = pc_monitoring_status(
            pc_observation, self.transaction.store.state["engine"], now
        )
        if self.transaction.history is not None:
            try:
                self.history_snapshot = await self.hass.async_add_executor_job(
                    self.transaction.history.snapshot,
                    self.transaction.store.state["history_revision"],
                    now,
                )
            except (ValueError, OSError):
                self.history_snapshot = None
                raise UpdateFailed("Lady accepted history could not be read") from None
        notify_ok = True
        try:
            await self.notifier.async_drain()
        except (HomeAssistantError, OSError, TimeoutError):
            notify_ok = False
        pending = len(self.transaction.store.state["outbox"])
        if self.history_snapshot is not None:
            source_metadata = {}
            for name, value in zip(names[:3], values[:3], strict=True):
                observed = (
                    value.observed_at
                    if isinstance(value, TimedObservation) and health[name]
                    else None
                )
                if name == "dns":
                    # Keep the old Prometheus source labels as stable slots.
                    # Resolver addresses are read only from the config entry.
                    results = tuple(row.get("dns", {}).values())
                    for suffix, result in zip(("253", "254"), results, strict=False):
                        known = type(result.get("ok")) is bool
                        source_metadata["dns" + suffix] = {
                            "observed_at": observed,
                            "ok": health[name] and known,
                        }
                    for suffix in ("253", "254"):
                        source_metadata.setdefault(
                            "dns" + suffix, {"observed_at": None, "ok": False}
                        )
                else:
                    source_metadata[name] = {"observed_at": observed, "ok": health[name]}
            source_metadata["windows"] = {
                "observed_at": self._windows_observed_at,
                "ok": health["windows"],
            }
            measured = copy.deepcopy(row)
            measured["windows"] = {"agent": copy.deepcopy(batch.agent)}
            self.metrics_snapshot = {
                "observed_at": now,
                "row": measured,
                "sources": source_metadata,
                "engine": self.transaction.store.state["engine"],
                "engine_options": dict(getattr(self.transaction, "engine_options", {})),
                "pending": pending,
                "history": copy.deepcopy(self.history_snapshot),
            }
        if self.publish_heartbeat:
            self.hass.bus.async_fire(
                "lady_network_monitor_heartbeat",
                {"source": "service_route_sync", "pending": pending},
            )
        return {
            "observed_at": now,
            "sources": health,
            "pending": pending,
            "notification_ok": notify_ok,
            "pc_monitoring": pc_status,
            **result,
        }
