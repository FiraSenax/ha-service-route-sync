"""Atomic native HA checkpoint for incidents, cursors and queued delivery."""

import asyncio
import json
import re
from homeassistant.core import CoreState

from homeassistant.helpers.storage import Store
from .windows_protocol import parse_cursor
from .windows_cursor import WindowsSourceError
from .lady_schema import validate_engine, validate_queues


class LadyCheckpointError(ValueError):
    """A checkpoint is invalid or superseded; never expose private event data."""


async def await_durable(operation):
    """Keep the shared writer lock until executor work really finishes.

    Cancelling an await does not stop its executor thread. Releasing the lock
    early could let that old thread overwrite a newer accepted revision.
    """
    task = asyncio.create_task(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    return cancelled


def validate_checkpoint(raw):
    fields = {"engine", "cursor", "outbox", "tracked_port", "deferred_events"}
    if (
        not isinstance(raw, dict)
        or not fields <= set(raw)
        or not set(raw) <= fields | {"history_revision", "history_epoch"}
    ):
        raise LadyCheckpointError("Unexpected checkpoint fields")
    if "history_revision" in raw and (
        type(raw["history_revision"]) is not int or not 0 <= raw["history_revision"] <= 2**63 - 1
    ):
        raise LadyCheckpointError("Invalid history revision")
    if "history_epoch" in raw and (
        not isinstance(raw["history_epoch"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", raw["history_epoch"])
        or not raw.get("history_revision")
    ):
        raise LadyCheckpointError("Invalid imported history epoch")
    if not isinstance(raw["engine"], dict):
        raise LadyCheckpointError("Invalid incident state")
    for key in ("outbox", "deferred_events"):
        if (
            not isinstance(raw[key], list)
            or len(raw[key]) > 1000
            or any(not isinstance(row, dict) for row in raw[key])
        ):
            raise LadyCheckpointError("Invalid event queue")
    cursor = raw["cursor"]
    if cursor is not None:
        try:
            parse_cursor(cursor, schema=2 if isinstance(cursor, dict) and "day" in cursor else 1)
        except WindowsSourceError:
            raise LadyCheckpointError("Invalid Windows cursor") from None
    if raw["tracked_port"] is not None and not isinstance(raw["tracked_port"], dict):
        raise LadyCheckpointError("Invalid tracked port state")
    try:
        validate_engine(raw["engine"])
        validate_queues(raw["outbox"], raw["deferred_events"])
        port = raw["tracked_port"]
        if port is not None and (
            set(port) != {"switch", "port"}
            or not isinstance(port["switch"], str)
            or not 1 <= len(port["switch"]) <= 200
            or type(port["port"]) is not int
            or not 1 <= port["port"] <= 65535
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise LadyCheckpointError("Invalid persisted state schema") from None
    try:
        encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 2000000:
            raise LadyCheckpointError("Checkpoint exceeds size bound")
        return json.loads(encoded)
    except (TypeError, ValueError, UnicodeError):
        raise LadyCheckpointError("Checkpoint is not valid bounded JSON") from None


class LadyCheckpointStore:
    def __init__(self, hass, entry_id):
        if not isinstance(entry_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", entry_id):
            raise LadyCheckpointError("Invalid checkpoint identity")
        self.store = Store(
            hass,
            1,
            f"service_route_sync.lady.{entry_id}",
            private=True,
            atomic_writes=True,
            serialize_in_event_loop=False,
        )
        locks = hass.data.setdefault("service_route_sync_lady_storage_locks", {})
        self.lock = locks.setdefault(entry_id, asyncio.Lock())
        self.hass = hass
        self._revision = 0
        self._state = None

    @property
    def revision(self):
        return self._revision

    @property
    def state(self):
        return validate_checkpoint(self._state) if self._state is not None else None

    async def async_load(self):
        async with self.lock:
            value = await self.store.async_load()
            if value is None:
                return None
            if (
                not isinstance(value, dict)
                or set(value) != {"revision", "checkpoint"}
                or type(value["revision"]) is not int
                or value["revision"] < 0
            ):
                raise LadyCheckpointError("Invalid stored checkpoint")
            checkpoint = validate_checkpoint(value["checkpoint"])
            if checkpoint.get("history_revision", 0) > value["revision"]:
                raise LadyCheckpointError("History revision exceeds checkpoint")
            self._revision, self._state = value["revision"], checkpoint
            return validate_checkpoint(checkpoint)

    async def async_commit(self, checkpoint, *, expected_revision, before_save=None):
        candidate = validate_checkpoint(checkpoint)
        async with self.lock:
            if self.hass.state is CoreState.stopping:
                raise LadyCheckpointError("Checkpoint cannot change during shutdown")
            if type(expected_revision) is not int or expected_revision != self.revision:
                raise LadyCheckpointError("Checkpoint was superseded")
            # A second Store instance must not overwrite a newer committed
            # cursor. The shared per-key lock serializes load/check/write.
            persisted = await self.store.async_load()
            if persisted is None:
                disk_revision = 0
            elif (
                isinstance(persisted, dict)
                and set(persisted) == {"revision", "checkpoint"}
                and type(persisted["revision"]) is int
                and persisted["revision"] >= 0
            ):
                validate_checkpoint(persisted["checkpoint"])
                disk_revision = persisted["revision"]
            else:
                raise LadyCheckpointError("Invalid stored checkpoint")
            if disk_revision != expected_revision:
                raise LadyCheckpointError("Stored checkpoint was superseded")
            revision = self.revision + 1
            if candidate.get("history_revision", 0) > revision:
                raise LadyCheckpointError("History revision exceeds checkpoint")
            if before_save is not None:
                # Runs only after the persisted CAS check, under the shared
                # lock. A stale writer cannot prune an accepted history batch.
                if await await_durable(before_save()):
                    raise asyncio.CancelledError

            async def save_checked():
                # HA Store defers saves initiated during stopping. Do not
                # mistake that deferred write for a durable accepted cursor.
                if self.hass.state is CoreState.stopping:
                    raise LadyCheckpointError("Checkpoint cannot change during shutdown")
                await self.store.async_save({"revision": revision, "checkpoint": candidate})

            cancelled = await await_durable(save_checked())
            # A failed save must leave the accepted cursor and queues unchanged.
            self._revision, self._state = revision, candidate
            if cancelled:
                raise asyncio.CancelledError
            return revision
