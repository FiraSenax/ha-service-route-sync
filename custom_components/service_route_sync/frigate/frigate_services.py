"""Admin-only fixed Frigate recovery action; ordinary shell services are not used."""

import voluptuous as vol
from typing import NotRequired, Protocol, TypedDict

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.service import async_register_admin_service

from ..backends.gateway_ssh import GatewayTransportError
from ..const import DOMAIN


class FrigateRestartResponse(TypedDict):
    restart_requested: bool
    attempt_epoch: NotRequired[float]
    container_id: NotRequired[str]


class FrigateActor(Protocol):
    @property
    def restart_enabled(self) -> bool: ...

    async def async_request_restart(self) -> FrigateRestartResponse: ...


def register_frigate_services(hass):
    if hass.services.has_service(DOMAIN, "restart_frigate"):
        return

    async def restart(call):
        entry = hass.config_entries.async_get_entry(call.data["entry_id"])
        actor = getattr(getattr(entry, "runtime_data", None), "frigate_actor", None)
        if (
            entry is None
            or entry.domain != DOMAIN
            or actor is None
            or getattr(entry, "state", None) is not ConfigEntryState.LOADED
        ):
            raise ServiceValidationError("Frigate recovery is not configured")
        if actor.restart_enabled is not True:
            raise ServiceValidationError("Frigate recovery is in observation mode")
        try:
            return await actor.async_request_restart()
        except (GatewayTransportError, OSError, ValueError):
            raise ServiceValidationError("Frigate restart request could not be confirmed") from None

    # HA checks authenticated callers before invoking the handler. Trusted
    # system automation contexts are permitted; API callers cannot forge them.
    async_register_admin_service(
        hass,
        DOMAIN,
        "restart_frigate",
        restart,
        schema=vol.Schema({vol.Required("entry_id"): str}),
        supports_response=SupportsResponse.OPTIONAL,
    )
