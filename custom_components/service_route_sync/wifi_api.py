"""Administrator-only bounded access to private wireless history."""

import asyncio
import probatio

from homeassistant.components import websocket_api

from .const import DOMAIN
from .wifi_history import WifiHistoryError


def bounded_integer(maximum):
    def validate(value):
        if type(value) is not int or not 0 <= value <= maximum:
            raise probatio.Invalid("Invalid history cursor")
        return value

    return validate


async def finish_executor(future):
    """Cancellation cannot stop a thread; retain the request lock until it ends."""
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        future.exception()
        raise asyncio.CancelledError
    return future.result()


def register_wifi_api(hass):
    locks = {}

    @websocket_api.require_admin
    @websocket_api.websocket_command(
        {
            "type": "service_route_sync/wifi_history",
            probatio.Required("entry_id"): str,
            probatio.Required("source"): probatio.In(["legacy", "observations"]),
            probatio.Optional("after", default=0): bounded_integer(2**63 - 1),
            probatio.Optional("ceiling"): bounded_integer(2**63 - 1),
            probatio.Optional("limit", default=100): probatio.All(
                bounded_integer(100), probatio.Range(min=1)
            ),
        }
    )
    @websocket_api.async_response
    async def history_page(hass, connection, msg):
        entry = hass.config_entries.async_get_entry(msg["entry_id"])
        wifi = getattr(getattr(entry, "runtime_data", None), "wifi", None)
        if entry is None or entry.domain != DOMAIN or wifi is None or wifi.history is None:
            connection.send_error(msg["id"], "not_available", "Wireless history is not loaded")
            return
        lock = locks.setdefault(entry.entry_id, asyncio.Lock())
        if lock.locked():
            connection.send_error(msg["id"], "busy", "Wireless history request is already running")
            return
        async with lock:
            try:
                result = await finish_executor(
                    hass.async_add_executor_job(
                        wifi.history.read_page,
                        msg["source"],
                        msg.get("after", 0),
                        msg.get("ceiling"),
                        msg.get("limit", 100),
                    )
                )
            except (WifiHistoryError, OSError):
                connection.send_error(
                    msg["id"], "history_unavailable", "Wireless history could not be read"
                )
                return
            connection.send_result(msg["id"], result)

    websocket_api.async_register_command(hass, history_page)
