"""Commit observations and pending notifications before any external delivery."""

import asyncio
import copy
import hashlib
import json
from dataclasses import asdict

from .lady_engine import LadyIncidentEngine
from .lady_store import LadyCheckpointError, validate_checkpoint
from .windows_queue import prepare_replay


class LadyTransaction:
    """One serialized producer; optimistic Store revisions reject other writers."""

    def __init__(self, store, history=None):
        self.store = store
        self.lock = asyncio.Lock()
        self.history = history

    async def async_sample(self, row, batch, now):
        async with self.lock:
            revision = self.store.revision
            candidate = self.store.state
            if candidate is None:
                candidate = {
                    "engine": {},
                    "cursor": None,
                    "outbox": [],
                    "tracked_port": None,
                    "deferred_events": [],
                }
            replay = prepare_replay(candidate["deferred_events"], batch.events, now)
            observation = copy.deepcopy(row)
            observation["windows"] = {
                "events": list(replay.due),
                "agent": copy.deepcopy(batch.agent),
            }
            observation["windows_ok"] = batch.heartbeat_ok
            engine = LadyIncidentEngine(candidate["engine"])
            events = engine.sample(observation, now)
            cursor = asdict(batch.cursor) if batch.cursor is not None else candidate["cursor"]
            for index, event in enumerate(events):
                identity = hashlib.sha256(
                    json.dumps(
                        {
                            "revision": revision + 1,
                            "cursor": cursor,
                            "index": index,
                            "event": event,
                        },
                        sort_keys=True,
                        allow_nan=False,
                    ).encode()
                ).hexdigest()
                candidate["outbox"].append({"delivery_id": identity, "event": event})
            candidate.update(
                engine=engine.s,
                cursor=cursor,
                deferred_events=replay.checkpoint,
                tracked_port=copy.deepcopy(
                    observation.get("tracked_port", candidate["tracked_port"])
                ),
            )
            # Validation or save failure leaves all accepted state unchanged.
            accepted_history = candidate.get("history_revision", 0)
            if self.history is not None:
                candidate["history_revision"] = revision + 1
            candidate = validate_checkpoint(candidate)

            async def prepare_history():
                await self.store.hass.async_add_executor_job(
                    self.history.prepare, revision + 1, accepted_history, now, observation, events
                )

            await self.store.async_commit(
                candidate,
                expected_revision=revision,
                before_save=prepare_history if self.history is not None else None,
            )
            return {"queued": len(events), "expired_windows_events": replay.expired}

    async def async_acknowledge(self, delivery_id):
        """Only acknowledge after the consumer confirms this stable delivery ID."""
        if not isinstance(delivery_id, str) or len(delivery_id) != 64:
            raise LadyCheckpointError("Invalid delivery identity")
        async with self.lock:
            revision = self.store.revision
            candidate = self.store.state
            if candidate is None:
                return False
            kept = [row for row in candidate["outbox"] if row.get("delivery_id") != delivery_id]
            if len(kept) == len(candidate["outbox"]):
                return False
            candidate["outbox"] = kept
            await self.store.async_commit(candidate, expected_revision=revision)
            return True
