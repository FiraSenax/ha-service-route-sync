"""Actual HA entity/coordinator semantics for restricted gateway observations."""

from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from custom_components.service_route_sync.backends.gateway_ssh import GatewayTransportError
from custom_components.service_route_sync.binary_sensor import GatewayProtocolSensor
from custom_components.service_route_sync.gateway_coordinator import GatewayCoordinator
from custom_components.service_route_sync.gateway_observation import parse_gateway_observation


def observation(now):
    timestamp = int(now.timestamp() * 1000)
    return {
        "schema": 1,
        "observed_at_ms": timestamp,
        "interfaces": [
            {
                "interface": interface,
                "observed_at_ms": timestamp,
                "link_up": True,
                "ipv4_probe": True,
                "ipv6_probe": True,
                "ipv6_default": True,
                "ipv6_address": True,
                "delegation_known": True,
                "delegated_prefix": "2001:4860:1234:5600::/56",
            }
            for interface in ("ppp0", "eth1")
        ],
    }


class GatewayRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hass = HomeAssistant(self.directory.name)
        self.addAsyncCleanup(self.hass.async_stop, force=True)
        self.entry = ConfigEntry(
            data={
                "gateway_observer_host": "192.168.1.1",
                "gateway_observer_key_id": "a" * 32,
                "gateway_primary_interface": "ppp0",
                "gateway_secondary_interface": "eth1",
            },
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
        self.coordinator = GatewayCoordinator(self.hass, self.entry)
        self.addAsyncCleanup(self.coordinator.async_shutdown)

    def load(self, raw, now):
        self.coordinator.data = parse_gateway_observation(
            raw, now, primary_interface="ppp0", secondary_interface="eth1"
        )

    async def test_missing_facts_are_unavailable_and_false_is_a_real_failure(self):
        now = dt_util.utcnow()
        raw = observation(now)
        sensor = GatewayProtocolSensor(self.coordinator, "primary", "ipv6")
        self.assertFalse(sensor.available)
        self.load(raw, now)
        self.assertTrue(sensor.available)
        self.assertTrue(sensor.is_on)
        raw["interfaces"][0]["ipv6_probe"] = False
        self.load(raw, now)
        self.assertTrue(sensor.available)
        self.assertFalse(sensor.is_on)
        raw["interfaces"][0]["ipv6_default"] = None
        self.load(raw, now)
        self.assertFalse(sensor.available)
        self.assertIsNone(sensor.is_on)
        self.assertIsNone(sensor.extra_state_attributes)

    async def test_future_and_stale_observations_are_not_available(self):
        now = dt_util.utcnow()
        for age in (-21, 1):
            sample_at = now + timedelta(seconds=age)
            self.load(observation(sample_at), sample_at + timedelta(milliseconds=1))
            sensor = GatewayProtocolSensor(self.coordinator, "secondary", "ipv4")
            self.assertFalse(sensor.available)

    async def test_carrier_loss_is_off_but_conflicting_probe_is_unavailable(self):
        now = dt_util.utcnow()
        raw = observation(now)
        raw["interfaces"][0].update(
            link_up=False,
            ipv4_probe=None,
            ipv6_probe=None,
            ipv6_default=None,
            ipv6_address=None,
            delegation_known=False,
            delegated_prefix=None,
        )
        self.load(raw, now)
        for protocol in ("ipv4", "ipv6"):
            sensor = GatewayProtocolSensor(self.coordinator, "primary", protocol)
            self.assertTrue(sensor.available)
            self.assertFalse(sensor.is_on)
        raw["interfaces"][0].update(ipv4_probe=True, ipv6_probe=True)
        self.load(raw, now)
        for protocol in ("ipv4", "ipv6"):
            sensor = GatewayProtocolSensor(self.coordinator, "primary", protocol)
            self.assertFalse(sensor.available)
            self.assertIsNone(sensor.is_on)

    async def test_protocol_handshake_and_transport_failures_are_sanitized(self):
        with (
            patch.object(type(self.coordinator.transport), "validate_files"),
            patch(
                "custom_components.service_route_sync.gateway_coordinator.read_gateway",
                new_callable=AsyncMock,
            ) as read,
        ):
            read.return_value = observation(dt_util.utcnow())
            await self.coordinator.async_config_entry_first_refresh()
            sensor = GatewayProtocolSensor(self.coordinator, "primary", "ipv4")
            self.assertTrue(sensor.available)
            read.side_effect = GatewayTransportError("private SSH details")
            with self.assertRaises(UpdateFailed) as caught:
                await self.coordinator._async_update_data()
            self.assertNotIn("private", str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)
            await self.coordinator.async_refresh()
            self.assertFalse(sensor.available)

    async def test_missing_credential_files_do_not_start_ssh(self):
        with patch(
            "custom_components.service_route_sync.gateway_coordinator.read_gateway",
            new_callable=AsyncMock,
        ) as read:
            with self.assertRaises(UpdateFailed):
                await self.coordinator._async_update_data()
            read.assert_not_awaited()
        self.assertFalse(list(Path(self.directory.name).rglob("observer_ed25519")))
