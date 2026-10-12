"""Native HA client collector lifecycle and partial-failure checks."""

from pathlib import Path
import sys
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_ha import test_network_runtime as network_tests
from custom_components.service_route_sync.client_coordinator import (
    ClientCoordinator,
    validate_profiles,
)
from custom_components.service_route_sync.client_observation import ClientDataError
from custom_components.service_route_sync.core import SyncError
from custom_components.service_route_sync.diagnostics import async_get_config_entry_diagnostics
from custom_components.service_route_sync.runtime import RuntimeData
from custom_components.service_route_sync.sensor import ClientConnectionSensor, ClientPortSensor
from custom_components.service_route_sync.binary_sensor import ClientOnlineSensor
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

MAC = "02:00:00:00:00:01"


class ClientRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = network_tests.NetworkRuntimeTests.asyncTearDown

    async def asyncSetUp(self):
        await network_tests.NetworkRuntimeTests.asyncSetUp(self)
        await self.coordinator.async_shutdown()
        self.coordinator = ClientCoordinator(self.hass, self.entry, [{"id": "pc", "macs": [MAC]}])
        self.coordinator.backend.read_clients = AsyncMock(
            return_value=[{"mac": MAC, "last_seen": dt_util.utcnow().timestamp(), "is_wired": True}]
        )
        self.coordinator.backend.read_devices = AsyncMock(return_value=[])

    async def test_complete_snapshot_and_profile_immutability(self):
        result = await self.coordinator._async_update_data()
        self.assertTrue(result.clients["pc"].online)
        with self.assertRaises(TypeError):
            result.clients["other"] = result.clients["pc"]

    async def test_diagnostics_omit_client_identities_and_raw_topology(self):
        await self.coordinator.async_config_entry_first_refresh()
        self.entry.runtime_data = RuntimeData(clients=self.coordinator)
        report = await async_get_config_entry_diagnostics(self.hass, self.entry)
        self.assertEqual(
            set(report["clients"]), {"last_update_success", "observed_at", "configured_count"}
        )
        self.assertEqual(report["clients"]["configured_count"], 1)
        self.assertNotIn(MAC, str(report))

    async def test_partial_failure_preserves_snapshot_and_marks_failed(self):
        await self.coordinator.async_config_entry_first_refresh()
        previous = self.coordinator.data
        self.coordinator.backend.read_devices.side_effect = SyncError("private-value")
        await self.coordinator.async_refresh()
        self.assertIs(self.coordinator.data, previous)
        self.assertFalse(self.coordinator.last_update_success)
        with self.assertRaises(UpdateFailed) as error:
            await self.coordinator._async_update_data()
        self.assertNotIn("private-value", str(error.exception))
        self.coordinator.backend.read_devices.side_effect = None
        await self.coordinator.async_refresh()
        self.assertTrue(self.coordinator.last_update_success)

    async def test_complete_empty_list_really_means_offline(self):
        self.coordinator.backend.read_clients.return_value = []
        result = await self.coordinator._async_update_data()
        self.assertFalse(result.clients["pc"].online)

    async def test_connection_sensor_explains_wired_diagnostics(self):
        sensor = ClientConnectionSensor(self.coordinator, "pc")
        await self.coordinator.async_config_entry_first_refresh()
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, "wired")
        self.coordinator.backend.read_clients.return_value = [
            {"mac": MAC, "last_seen": dt_util.utcnow().timestamp(), "is_wired": False}
        ]
        await self.coordinator.async_refresh()
        self.assertEqual(sensor.native_value, "wifi")
        self.coordinator.backend.read_clients.return_value = []
        await self.coordinator.async_refresh()
        self.assertEqual(sensor.native_value, "offline")
        self.coordinator.backend.read_clients.side_effect = SyncError("private")
        await self.coordinator.async_refresh()
        self.assertFalse(sensor.available)

    async def test_entities_reject_stale_future_and_failed_samples(self):
        sensor = ClientOnlineSensor(self.coordinator, "pc", "online")
        self.assertFalse(sensor.available)
        await self.coordinator.async_config_entry_first_refresh()
        self.assertTrue(sensor.available)
        self.assertTrue(sensor.is_on)
        original = self.coordinator.data
        for delta in [-76, 2]:
            self.coordinator.async_set_updated_data(
                replace(original, observed_at=dt_util.utcnow() + timedelta(seconds=delta))
            )
            self.assertFalse(sensor.available)
        self.coordinator.async_set_updated_data(original)
        self.coordinator.backend.read_clients.side_effect = SyncError("failure")
        await self.coordinator.async_refresh()
        self.assertFalse(sensor.available)

    async def test_port_zero_missing_and_offline_semantics(self):
        self.coordinator.backend.read_devices.return_value = [
            {
                "mac": "02:00:00:00:00:02",
                "last_seen": dt_util.utcnow().timestamp(),
                "port_table": [
                    {
                        "port_idx": 1,
                        "up": True,
                        "speed": 2500,
                        "rx_errors": 0,
                        "last_connection": {"mac": MAC, "connected": True},
                    }
                ],
            }
        ]
        await self.coordinator.async_config_entry_first_refresh()
        zero = ClientPortSensor(self.coordinator, "pc", "rx_errors")
        missing = ClientPortSensor(self.coordinator, "pc", "tx_errors")
        speed = ClientPortSensor(self.coordinator, "pc", "speed_mbit_s")
        self.assertTrue(zero.available)
        self.assertEqual(zero.native_value, 0)
        self.assertFalse(missing.available)
        self.assertEqual(speed.native_value, 2500)
        self.assertEqual(speed.native_unit_of_measurement, "Mbit/s")
        self.coordinator.backend.read_clients.return_value = []
        await self.coordinator.async_refresh()
        self.assertFalse(speed.available)
        online = ClientOnlineSensor(self.coordinator, "pc", "online")
        self.assertTrue(online.available)
        self.assertFalse(online.is_on)

    async def test_alias_ambiguity_and_duplicate_profiles_rejected(self):
        for rows in [
            [],
            [{"id": "pc", "macs": [MAC, MAC]}],
            [{"id": "pc", "macs": [MAC]}, {"id": "other", "macs": [MAC]}],
            [{"id": "pc", "macs": ["bad"]}],
        ]:
            with self.assertRaises(ClientDataError):
                validate_profiles(rows)


if __name__ == "__main__":
    unittest.main(defaultTest="ClientRuntimeTests")
