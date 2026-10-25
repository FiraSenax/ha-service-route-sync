"""Authenticated compatibility metrics in HA's existing HTTP server."""

from aiohttp import web
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.config_entries import ConfigEntryState
from homeassistant.components.http import HomeAssistantView, KEY_HASS_USER
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .client_metrics import render_clients
from .const import DOMAIN
from .network_metrics import MetricDataError
from .work_probes import render_work_probes
from .monitoring_metrics import render_monitoring


class NetworkMetricsView(HomeAssistantView):
    url = "/api/service_route_sync/{entry_id}/metrics/gaming"
    name = "api:service_route_sync:gaming_metrics"
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, entry_id):
        user = request.get(KEY_HASS_USER)
        if user is None:
            return web.Response(status=401)
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return web.Response(status=404)
        if entry.state is not ConfigEntryState.LOADED:
            return web.Response(status=503)
        settings = {**entry.data, **entry.options}
        runtime = getattr(entry, "runtime_data", None)
        if (
            not settings.get("network_metrics_enabled")
            or runtime is None
            or runtime.clients is None
            or runtime.client_probes is None
            or runtime.work_probes is None
        ):
            return web.Response(status=503)
        registry = er.async_get(self.hass)
        client_entities = [
            row.entity_id
            for row in er.async_entries_for_config_entry(registry, entry_id)
            if row.unique_id.startswith(
                tuple(entry_id + "_client_" + key + "_" for key in runtime.clients.profiles)
            )
        ]
        if not client_entities or any(
            not user.permissions.check_entity(entity, POLICY_READ) for entity in client_entities
        ):
            return web.Response(status=403)
        clients, probes, work = runtime.clients, runtime.client_probes, runtime.work_probes
        now = dt_util.utcnow()
        probe_data = probes.data if probes.last_update_success else None
        try:
            text = render_clients(
                tuple(clients.profiles),
                clients.data,
                clients.last_update_success,
                probe_data.probes if probe_data else {},
                probe_data.routes if probe_data else {},
                now,
                probe_cycle_at=probe_data.observed_at if probe_data else None,
            ) + render_work_probes(work.data or {}, now)
        except (MetricDataError, ValueError, TypeError):
            # A malformed snapshot is a failed scrape, not a partial success.
            return web.Response(status=503)
        return web.Response(
            text=text,
            headers={
                "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
                "Cache-Control": "no-store",
            },
        )


class MonitoringMetricsView(HomeAssistantView):
    url = "/api/service_route_sync/{entry_id}/metrics/monitoring"
    name = "api:service_route_sync:monitoring_metrics"
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, entry_id):
        user = request.get(KEY_HASS_USER)
        if user is None:
            return web.Response(status=401)
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return web.Response(status=404)
        settings = {**entry.data, **entry.options}
        runtime = getattr(entry, "runtime_data", None)
        monitoring = getattr(runtime, "monitoring", None)
        if (
            entry.state is not ConfigEntryState.LOADED
            or not settings.get("network_metrics_enabled")
            or not settings.get("monitoring_enabled")
            or monitoring is None
        ):
            return web.Response(status=503)
        registry = er.async_get(self.hass)
        entities = [
            row.entity_id
            for row in er.async_entries_for_config_entry(registry, entry_id)
            if row.unique_id.startswith(
                (
                    entry_id + "_monitoring_",
                    entry_id
                    + "_client_"
                    + settings.get("monitoring_profile", {}).get("id", "unconfigured")
                    + "_",
                )
            )
        ]
        if not entities or any(
            not user.permissions.check_entity(entity, POLICY_READ) for entity in entities
        ):
            return web.Response(status=403)
        coordinator = monitoring.incidents
        if not coordinator.last_update_success or coordinator.metrics_snapshot is None:
            return web.Response(status=503)
        now = dt_util.utcnow()
        bandwidth = None
        network = getattr(runtime, "network", None)
        if (
            network is not None
            and network.last_update_success
            and network.data is not None
            and network.data.primary is not None
        ):
            primary = network.data.primary
            bandwidth = {
                "observed_at": primary.observed_at.timestamp(),
                "upload": primary.upload_mbit_s,
                "download": primary.download_mbit_s,
            }
        try:
            text = render_monitoring(coordinator.metrics_snapshot, now.timestamp(), bandwidth)
        except (MetricDataError, ValueError, TypeError, KeyError):
            return web.Response(status=503)
        return web.Response(
            text=text,
            headers={
                "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
                "Cache-Control": "no-store",
            },
        )


def register_metrics_view(hass):
    data = hass.data.setdefault(DOMAIN, {})
    if not data.get("metrics_view_registered"):
        hass.http.register_view(NetworkMetricsView(hass))
        hass.http.register_view(MonitoringMetricsView(hass))
        data["metrics_view_registered"] = True
