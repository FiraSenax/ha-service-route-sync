"""Administrator acknowledgement after a separately audited IPv6 transaction."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.service import async_register_admin_service
import voluptuous as vol

from .backends.gateway_ssh import GatewayTransportError
from .const import DOMAIN


def register_ipv6_services(hass):
    if hass.services.has_service(DOMAIN, "acknowledge_ipv6_policy_review"):
        return

    async def acknowledge(call):
        entry = hass.config_entries.async_get_entry(call.data["entry_id"])
        actor = getattr(getattr(entry, "runtime_data", None), "ipv6_actor", None)
        if (
            entry is None
            or entry.domain != DOMAIN
            or actor is None
            or entry.state is not ConfigEntryState.LOADED
        ):
            raise ServiceValidationError("IPv6 policy actor is unavailable")
        try:
            await actor.async_acknowledge_review(call.data["digest"], call.data["mode"])
        except (GatewayTransportError, OSError, ValueError):
            raise ServiceValidationError("IPv6 policy acknowledgement was refused") from None

    async_register_admin_service(
        hass,
        DOMAIN,
        "acknowledge_ipv6_policy_review",
        acknowledge,
        schema=vol.Schema(
            {
                vol.Required("entry_id"): str,
                vol.Required("digest"): vol.Match(r"[0-9a-f]{64}"),
                vol.Required("mode"): vol.In(("dsl", "starlink")),
            }
        ),
    )
