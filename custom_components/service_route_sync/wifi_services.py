"""Import only an administrator-staged, checksum-addressed private CSV."""

import hashlib
import asyncio
import os
import re
import stat
import voluptuous as vol

from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN
from .wifi_api import finish_executor
from .wifi_history import WifiHistoryError


def import_staged(history, entry_id, digest):
    directory = history.path.parent
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise WifiHistoryError("Unsafe WiFi import directory")
    identity = hashlib.sha256(entry_id.encode()).hexdigest()
    path = directory / f"{identity}.{digest}.csv"
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        raise WifiHistoryError("Unsafe WiFi import source")
    return history.import_csv(path, digest)


def checksum(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise vol.Invalid("Expected SHA-256 checksum")
    return value


def register_wifi_services(hass):
    if hass.services.has_service(DOMAIN, "import_wifi_history"):
        return
    locks = {}

    async def import_history(call):
        if not call.context.user_id:
            raise ServiceValidationError("An authenticated administrator is required for import")
        entry = hass.config_entries.async_get_entry(call.data["entry_id"])
        wifi = getattr(getattr(entry, "runtime_data", None), "wifi", None)
        if entry is None or entry.domain != DOMAIN or wifi is None or wifi.history is None:
            raise ServiceValidationError("Wireless history must be loaded for import")
        lock = locks.setdefault(entry.entry_id, asyncio.Lock())
        if lock.locked():
            raise ServiceValidationError("Wireless history import is already running")
        async with lock:
            try:
                return await finish_executor(
                    hass.async_add_executor_job(
                        import_staged, wifi.history, entry.entry_id, call.data["sha256"]
                    )
                )
            except (WifiHistoryError, OSError):
                raise ServiceValidationError(
                    "Staged wireless history could not be imported"
                ) from None

    async_register_admin_service(
        hass,
        DOMAIN,
        "import_wifi_history",
        import_history,
        schema=vol.Schema({vol.Required("entry_id"): str, vol.Required("sha256"): checksum}),
        supports_response=SupportsResponse.OPTIONAL,
    )
