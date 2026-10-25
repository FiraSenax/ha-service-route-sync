"""Exercise HA's real coordinator and entity classes, without router writes."""

from datetime import timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_ha import CONNECTION

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.service_route_sync.core import SyncError
from custom_components.service_route_sync.network import parse_gateway
from custom_components.service_route_sync.network_coordinator import NetworkCoordinator
from custom_components.service_route_sync.sensor import NetworkMetricSensor, NetworkUpdateSensor
from custom_components.service_route_sync.binary_sensor import NetworkLinkSensor


class NetworkRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.entry = ConfigEntry(
            data={**CONNECTION, "network_gateway_id": "gateway"},
            options={},
            domain="service_route_sync",
            title="Test",
            unique_id="parent",
            version=1,
            minor_version=1,
            source="user",
            discovery_keys={},
            subentries_data=[],
            state=ConfigEntryState.SETUP_IN_PROGRESS,
        )
        self.coordinator = NetworkCoordinator(self.hass, self.entry)
        self.coordinator.backend.read_gateways = AsyncMock()

    async def asyncTearDown(self):
        await self.coordinator.async_shutdown()
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    async def test_valid_zero_and_units_are_available_before_first_update(self):
        self.coordinator.backend.read_gateways.return_value = {
            "gateway": {"state": 1, "wan1": {"up": False, "rx_bytes-r": 0}},
        }
        sensor = NetworkMetricSensor(self.coordinator, "primary", "download_mbit_s")
        self.assertEqual(sensor.native_unit_of_measurement, "Mbit/s")
        self.assertFalse(sensor.available)
        await self.coordinator.async_config_entry_first_refresh()
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, 0)
        link = NetworkLinkSensor(self.coordinator, "primary", "link")
        self.assertTrue(link.available)
        self.assertFalse(link.is_on)
        self.assertFalse(NetworkMetricSensor(self.coordinator, "secondary", "latency_ms").available)

    async def test_unknown_link_is_not_rolling_availability(self):
        self.coordinator.data = parse_gateway({"wan1": {"availability": 100}}, dt_util.utcnow())
        self.assertFalse(NetworkLinkSensor(self.coordinator, "primary", "link").available)

    async def test_stale_and_future_samples_are_unavailable(self):
        sensor = NetworkMetricSensor(self.coordinator, "primary", "latency_ms")
        for offset in (-21, 1):
            self.coordinator.data = parse_gateway(
                {"wan1": {"latency": 0}}, dt_util.utcnow() + timedelta(seconds=offset)
            )
            self.assertFalse(sensor.available)

    async def test_api_outage_retains_data_but_marks_entity_unavailable(self):
        self.coordinator.backend.read_gateways.return_value = {
            "gateway": {"state": 1, "wan1": {"latency": 1}},
        }
        await self.coordinator.async_config_entry_first_refresh()
        sensor = NetworkMetricSensor(self.coordinator, "primary", "latency_ms")
        self.coordinator.backend.read_gateways.side_effect = SyncError("unavailable")
        await self.coordinator.async_refresh()
        self.assertFalse(sensor.available)
        self.assertEqual(sensor.native_value, 1)
        self.coordinator.backend.read_gateways.side_effect = None
        await self.coordinator.async_refresh()
        self.assertTrue(sensor.available)

    async def test_offline_or_missing_gateway_rejects_cached_measurements(self):
        for data in ({}, {"gateway": {"state": 0, "wan1": {"availability": 100}}}):
            self.coordinator.backend.read_gateways.return_value = data
            with self.assertRaises(UpdateFailed):
                await self.coordinator._async_update_data()

    async def test_unload_cancels_periodic_refresh(self):
        with patch.object(self.coordinator, "_schedule_refresh") as schedule:
            remove = self.coordinator.async_add_listener(lambda: None)
            schedule.assert_called_once()
            remove()
        await self.coordinator.async_shutdown()
        self.assertTrue(self.coordinator._shutdown_requested)
        self.assertIsNone(self.coordinator._unsub_refresh)

    async def test_observation_timestamp_works_with_secondary_wan_only(self):
        self.coordinator.data = parse_gateway({"wan2": {"up": True}}, dt_util.utcnow())
        sensor = NetworkUpdateSensor(self.coordinator)
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, self.coordinator.data.observed_at)
        self.assertEqual(sensor.unique_id, f"{self.entry.entry_id}_network_updated")
        primary = NetworkMetricSensor(self.coordinator, "primary", "latency_ms")
        self.assertFalse(primary.available)

    async def test_failing_cloud_feature_recovers_soon_without_logging_private_body(self):
        from custom_components.service_route_sync.coordinator import RouteCoordinator

        with patch("custom_components.service_route_sync.coordinator.async_get_clientsession"):
            routes = RouteCoordinator(self.hass, self.entry)
        self.addAsyncCleanup(routes.async_shutdown)
        routes.feeds.all_prefixes = AsyncMock(side_effect=SyncError("private-example"))
        with self.assertRaises(UpdateFailed) as error:
            await routes._async_update_data()
        self.assertEqual(routes.update_interval, timedelta(minutes=1))
        self.assertNotIn("private-example", str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)
        routes.settings["bindings"] = {}
        routes.feeds.all_prefixes.side_effect = None
        routes.feeds.all_prefixes.return_value = {}
        routes.backend.read_routes = AsyncMock(return_value={})
        routes.backend.write_route = AsyncMock()
        result = await routes._async_update_data()
        self.assertEqual(result["status"], "preview")
        self.assertEqual(routes.update_interval, timedelta(hours=24))
        routes.backend.write_route.assert_not_awaited()
