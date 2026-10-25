from datetime import timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.util import dt as dt_util
from custom_components.service_route_sync.frigate.frigate_coordinator import FrigateObservation
from custom_components.service_route_sync.frigate.frigate_entity import (
    FrigateStatusBinarySensor,
    FrigateTimestampSensor,
)
from custom_components.service_route_sync.runtime import RuntimeData
from custom_components.service_route_sync.diagnostics import async_get_config_entry_diagnostics


class FrigateEntityTests(unittest.TestCase):
    def coordinator(self):
        return SimpleNamespace(
            entry=SimpleNamespace(entry_id="test"),
            last_update_success=True,
            binding_current=True,
            restart_enabled=False,
            data=FrigateObservation(dt_util.utcnow(), False, None),
        )

    def test_stopped_container_is_false_and_no_private_attributes(self):
        coordinator = self.coordinator()
        sensor = FrigateStatusBinarySensor(coordinator, "running")
        self.assertTrue(sensor.available)
        self.assertFalse(sensor.is_on)
        self.assertIsNone(sensor.extra_state_attributes)
        self.assertTrue(FrigateStatusBinarySensor(coordinator, "connection").is_on)
        self.assertFalse(FrigateStatusBinarySensor(coordinator, "restart_enabled").is_on)
        self.assertIsNone(FrigateTimestampSensor(coordinator, "started").native_value)

    def test_failed_stale_future_and_rebound_status_are_unavailable(self):
        coordinator = self.coordinator()
        sensor = FrigateStatusBinarySensor(coordinator, "running")
        coordinator.last_update_success = False
        self.assertFalse(sensor.available)
        coordinator.last_update_success = True
        for delta in (-76, 60):
            coordinator.data = FrigateObservation(
                dt_util.utcnow() + timedelta(seconds=delta), True, None
            )
            self.assertFalse(sensor.available)
        coordinator.data = FrigateObservation(dt_util.utcnow(), True, None)
        coordinator.binding_current = False
        self.assertFalse(sensor.available)


class FrigateDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostics_expose_only_operational_status(self):
        coordinator = SimpleNamespace(
            last_update_success=True,
            restart_enabled=False,
            data=FrigateObservation(dt_util.utcnow(), True, None),
            credential_directory="/private/secret-scope",
        )
        entry = SimpleNamespace(runtime_data=RuntimeData(frigate_actor=coordinator))
        report = await async_get_config_entry_diagnostics(None, entry)
        self.assertEqual(
            set(report["frigate"]),
            {"last_update_success", "observed_at", "running", "restart_enabled"},
        )
        self.assertNotIn("secret-scope", str(report))
