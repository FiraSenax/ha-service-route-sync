"""Maintain explicitly selected service destinations in existing router policies."""

from homeassistant.exceptions import ConfigEntryNotReady, ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .connection import requires_controller
from .routing.coordinator import RouteCoordinator
from .network.network_coordinator import NetworkCoordinator
from .network.gateway_coordinator import GatewayCoordinator
from .clients.client_coordinator import ClientCoordinator
from .clients.client_probe_coordinator import ClientProbeCoordinator
from .probes.work_probe_coordinator import WorkProbeCoordinator
from .metrics_view import register_metrics_view
from .runtime import RuntimeData
from .lifecycle import Feature, prepare_features
from .monitoring.monitoring_runtime import prepare_monitoring
from .monitoring.monitoring_services import register_monitoring_services
from .wifi.wifi_coordinator import prepare_wifi
from .wifi.wifi_api import register_wifi_api
from .wifi.wifi_services import register_wifi_services
from .frontend import register_frontend
from .frigate.frigate_services import register_frigate_services
from .frigate.frigate_coordinator import FrigateActorCoordinator
from .ipv6.ipv6_coordinator import IPv6Coordinator
from .ipv6.ipv6_services import register_ipv6_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass, config):
    await register_frontend(hass)
    register_monitoring_services(hass)
    register_wifi_api(hass)
    register_wifi_services(hass)
    register_frigate_services(hass)
    register_ipv6_services(hass)
    return True


async def async_setup_entry(hass, entry):
    settings = {**entry.data, **entry.options}
    if requires_controller(settings) and not all(
        settings.get(key) for key in ("unifi_host", "unifi_api_key")
    ):
        raise ConfigEntryAuthFailed("Configure this integration's own UniFi connection")
    runtime = RuntimeData()
    failures = await prepare_features(
        runtime,
        (
            Feature(
                "routes",
                bool(settings.get("services")),
                lambda: RouteCoordinator(hass, entry),
                initialize="load",
            ),
            Feature(
                "network",
                bool(settings.get("network_enabled")),
                lambda: NetworkCoordinator(hass, entry),
            ),
            Feature(
                "gateway",
                bool(settings.get("gateway_observer_enabled")),
                lambda: GatewayCoordinator(hass, entry),
            ),
            Feature(
                "ipv6_actor",
                settings.get("ipv6_actor_enabled") is True,
                lambda: IPv6Coordinator(hass, entry, runtime.gateway),
                requires=("gateway",),
                initialize="async_initialize",
            ),
            Feature(
                "clients",
                bool(settings.get("client_profiles")),
                lambda: ClientCoordinator(hass, entry, settings["client_profiles"]),
            ),
            Feature(
                "client_probes",
                bool(settings.get("client_probe_enabled")),
                lambda: ClientProbeCoordinator(hass, entry, runtime.clients),
                requires=("clients",),
            ),
            Feature(
                "work_probes",
                bool(settings.get("work_probe_enabled")),
                lambda: WorkProbeCoordinator(hass, entry),
            ),
            Feature(
                "wifi",
                bool(settings.get("wifi_history_enabled")),
                lambda: prepare_wifi(hass, entry, settings),
            ),
            Feature(
                "frigate_actor",
                settings.get("frigate_actor_enabled") is True,
                lambda: FrigateActorCoordinator(hass, entry),
            ),
            Feature(
                "monitoring",
                bool(settings.get("monitoring_enabled")),
                lambda: prepare_monitoring(hass, entry, settings),
            ),
        ),
    )
    coordinators = runtime.coordinators()
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
        await runtime.shutdown()
        raise ConfigEntryNotReady("The selected router or sources are unavailable") from failures[0]
    entry.runtime_data = runtime
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await runtime.shutdown()
        raise
    # These observations also feed the compatibility exporter. Keep them polling
    # even with disabled entities, and retry a failed initial probe handshake.
    for coordinator in (
        runtime.clients,
        runtime.client_probes,
        runtime.work_probes,
        runtime.wifi,
        runtime.frigate_actor,
        runtime.monitoring.clients if runtime.monitoring else None,
        runtime.monitoring.incidents if runtime.monitoring else None,
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
        await entry.runtime_data.shutdown()
    return unloaded


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)
