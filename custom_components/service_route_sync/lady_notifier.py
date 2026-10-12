"""Bounded native notification delivery with durable at-least-once retry."""

import asyncio

from homeassistant.exceptions import HomeAssistantError


class LadyNotifier:
    def __init__(self, hass, transaction):
        self.hass = hass
        self.transaction = transaction
        self.lock = asyncio.Lock()

    async def async_drain(self, *, maximum=4):
        """Only ack a completed service call; failed/cancelled calls stay queued.

        Pushover provides no idempotency key. A crash after sending but before
        persisting the acknowledgement can repeat a notification. Stable IDs
        prevent queue duplication, but do not promise exactly-once delivery.
        """
        if type(maximum) is not int or not 1 <= maximum <= 10:
            raise ValueError("Invalid notification batch bound")
        async with self.lock:
            delivered = 0
            for _ in range(maximum):
                state = self.transaction.store.state
                if state is None or not state["outbox"]:
                    break
                row = state["outbox"][0]
                event = row["event"]
                if not self.hass.services.has_service("notify", "pushover"):
                    raise HomeAssistantError("Lady notification service unavailable")
                title = (
                    "LadyOfGames · Netzwerk wieder unauffällig"
                    if event["recovery"]
                    else "LadyOfGames · Netzwerkdiagnose"
                )
                # Use the configured integration; no credentials or HTTP client.
                async with asyncio.timeout(20):
                    await self.hass.services.async_call(
                        "notify",
                        "pushover",
                        {"title": title, "message": event["message"], "data": {"priority": 0}},
                        blocking=True,
                    )
                await self.transaction.async_acknowledge(row["delivery_id"])
                delivered += 1
            return delivered
