"""Maintain explicitly selected service destinations in existing router policies."""

from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN, PLATFORMS
from .coordinator import RouteCoordinator
from .network_coordinator import NetworkCoordinator
from .gateway_coordinator import GatewayCoordinator
from .client_coordinator import ClientCoordinator
from .client_probe_coordinator import ClientProbeCoordinator
from .work_probe_coordinator import WorkProbeCoordinator
from .metrics_view import register_metrics_view
from .runtime import RuntimeData
from .lady_runtime import prepare_lady
from .backends.gateway_ssh import GatewayTransportError
from .lady_services import register_lady_services
from .wifi_coordinator import prepare_wifi
from .wifi_api import register_wifi_api
from .wifi_services import register_wifi_services
from .frontend import register_frontend
from .frigate_services import register_frigate_services
from .frigate_coordinator import FrigateActorCoordinator
from .ipv6_coordinator import IPv6Coordinator
from .ipv6_services import register_ipv6_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass, config):
    await register_frontend(hass)
    register_lady_services(hass)
    register_wifi_api(hass)
    register_wifi_services(hass)
    register_frigate_services(hass)
    register_ipv6_services(hass)
    return True


async def async_setup_entry(hass, entry):
    if entry.unique_id is None:
        hass.config_entries.async_update_entry(entry, unique_id=entry.data["unifi_entry_id"])
    settings = {**entry.data, **entry.options}
    runtime = RuntimeData()
    failures = []
    if settings.get("services"):
        runtime.routes = RouteCoordinator(hass, entry)
        await runtime.routes.load()
    if settings.get("network_enabled"):
        runtime.network = NetworkCoordinator(hass, entry)
    if settings.get("gateway_observer_enabled"):
        runtime.gateway = GatewayCoordinator(hass, entry)
    if settings.get("ipv6_actor_enabled") is True:
        if runtime.gateway is None:
            failures.append(ConfigEntryNotReady("IPv6 policy requires verified WAN observations"))
        else:
            try:
                runtime.ipv6_actor = IPv6Coordinator(hass, entry, runtime.gateway)
                await runtime.ipv6_actor.async_initialize()
            except (
                KeyError,
                ValueError,
                OSError,
                GatewayTransportError,
                ConfigEntryNotReady,
                UpdateFailed,
            ):
                runtime.ipv6_actor = None
                failures.append(ConfigEntryNotReady("IPv6 policy trust is not ready"))
    if settings.get("client_profiles"):
        runtime.clients = ClientCoordinator(hass, entry, settings["client_profiles"])
        if settings.get("client_probe_enabled"):
            runtime.client_probes = ClientProbeCoordinator(hass, entry, runtime.clients)
    if settings.get("work_probe_enabled"):
        runtime.work_probes = WorkProbeCoordinator(hass, entry)
    if settings.get("wifi_history_enabled"):
        runtime.wifi = prepare_wifi(hass, entry, settings)
    if settings.get("frigate_actor_enabled") is True:
        try:
            runtime.frigate_actor = FrigateActorCoordinator(hass, entry)
        except (ValueError, OSError, GatewayTransportError):
            failures.append(ConfigEntryNotReady("Frigate peer trust is not ready"))
    if settings.get("lady_enabled"):
        try:
            runtime.lady = await prepare_lady(hass, entry, settings)
        except (ValueError, OSError, GatewayTransportError, ConfigEntryNotReady):
            failures.append(ConfigEntryNotReady("Lady observation sources are not ready"))
    coordinators = (
        runtime.routes,
        runtime.network,
        runtime.gateway,
        runtime.ipv6_actor,
        runtime.clients,
        runtime.client_probes,
        runtime.work_probes,
        runtime.wifi,
        runtime.frigate_actor,
        runtime.lady.clients if runtime.lady else None,
        runtime.lady.incidents if runtime.lady else None,
    )
    if not any(coordinators):
        raise ConfigEntryNotReady("No feature is configured or ready")
    ready = False
    for coordinator in coordinators:
        if coordinator is not None:
            try:
                await coordinator.async_config_entry_first_refresh()
                ready = True
            except ConfigEntryNotReady as exc:
                failures.append(exc)
    if not ready:
        for coordinator in coordinators:
            if coordinator is not None:
                await coordinator.async_shutdown()
        raise ConfigEntryNotReady("The selected router or sources are unavailable") from failures[0]
    entry.runtime_data = runtime
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        for coordinator in coordinators:
            if coordinator is not None:
                await coordinator.async_shutdown()
        raise
    # These observations also feed the compatibility exporter. Keep them polling
    # even with disabled entities, and retry a failed initial probe handshake.
    for coordinator in (
        runtime.clients,
        runtime.client_probes,
        runtime.work_probes,
        runtime.wifi,
        runtime.frigate_actor,
        runtime.lady.clients if runtime.lady else None,
        runtime.lady.incidents if runtime.lady else None,
    ):
        if coordinator is not None:
            entry.async_on_unload(coordinator.async_add_listener(lambda: None))
    if settings.get("network_metrics_enabled"):
        register_metrics_view(hass)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = entry.runtime_data
        for coordinator in (
            runtime.routes,
            runtime.network,
            runtime.gateway,
            runtime.ipv6_actor,
            runtime.clients,
            runtime.client_probes,
            runtime.work_probes,
            runtime.wifi,
            runtime.frigate_actor,
            runtime.lady.clients if runtime.lady else None,
            runtime.lady.incidents if runtime.lady else None,
        ):
            if coordinator is not None:
                await coordinator.async_shutdown()
    return unloaded


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
