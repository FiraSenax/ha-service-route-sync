"""Maintain explicitly selected service destinations in existing router policies."""

from homeassistant.exceptions import ConfigEntryNotReady

from .const import PLATFORMS
from .coordinator import RouteCoordinator


async def async_setup_entry(hass, entry):
    if entry.unique_id is None:
        hass.config_entries.async_update_entry(entry, unique_id=entry.data["unifi_entry_id"])
    coordinator = RouteCoordinator(hass, entry)
    await coordinator.load()
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as exc:
        raise ConfigEntryNotReady("Service sources or selected UniFi router unavailable") from exc
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
