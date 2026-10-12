from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from custom_components.service_route_sync.frigate_coordinator import FrigateActorCoordinator
from custom_components.service_route_sync.backends.gateway_ssh import GatewayTransportError


class FrigateCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.entry = SimpleNamespace(
            entry_id="frigate-test",
            async_on_unload=Mock(),
            data={
                "frigate_actor_enabled": True,
                "frigate_actor_host": "172.20.1.12",
                "frigate_actor_key_id": "a" * 32,
            },
            options={},
        )
        self.coordinator = FrigateActorCoordinator(self.hass, self.entry)
        self.status = {"running": True, "started": "2026-10-12T04:00:00Z"}
        self.trust = patch(
            "custom_components.service_route_sync.frigate_coordinator.FrigateStatusSSHConfig.validate_files"
        )
        self.trust.start()
        self.addCleanup(self.trust.stop)

    async def asyncTearDown(self):
        await self.coordinator.async_shutdown()
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_observation_failure_retains_data_but_is_not_success(self):
        with patch(
            "custom_components.service_route_sync.frigate_coordinator.read_frigate_status",
            AsyncMock(return_value=self.status),
        ):
            await self.coordinator.async_refresh()
        previous = self.coordinator.data
        self.assertTrue(previous.running)
        with patch(
            "custom_components.service_route_sync.frigate_coordinator.read_frigate_status",
            AsyncMock(side_effect=GatewayTransportError("synthetic")),
        ):
            await self.coordinator.async_refresh()
        self.assertFalse(self.coordinator.last_update_success)
        self.assertIs(self.coordinator.data, previous)

    async def test_disabled_or_rebound_profile_never_sends_control(self):
        with patch(
            "custom_components.service_route_sync.frigate_coordinator.request_frigate_restart",
            AsyncMock(),
        ) as request:
            with self.assertRaises(GatewayTransportError):
                await self.coordinator.async_request_restart()
            self.entry.options = {
                "frigate_restart_enabled": True,
                "frigate_actor_host": "172.20.1.13",
            }
            with self.assertRaises(GatewayTransportError):
                await self.coordinator.async_request_restart()
            request.assert_not_awaited()

    async def test_disabling_during_executor_validation_stops_request(self):
        self.entry.options = {"frigate_restart_enabled": True}

        def disable():
            self.entry.options["frigate_restart_enabled"] = False

        with (
            patch(
                "custom_components.service_route_sync.frigate_coordinator.FrigateRestartSSHConfig.validate_files",
                side_effect=disable,
            ),
            patch(
                "custom_components.service_route_sync.frigate_coordinator.request_frigate_restart",
                AsyncMock(),
            ) as request,
        ):
            with self.assertRaises(GatewayTransportError):
                await self.coordinator.async_request_restart()
        request.assert_not_awaited()

    async def test_enabled_request_returns_only_public_receipt(self):
        self.entry.options = {"frigate_restart_enabled": True}
        receipt = {
            "restart_requested": True,
            "attempt_epoch": 10000,
            "container_id": "a" * 64,
            "protocol": 1,
        }
        with patch(
            "custom_components.service_route_sync.frigate_coordinator.request_frigate_restart",
            AsyncMock(return_value=receipt),
        ) as request:
            result = await self.coordinator.async_request_restart()
        self.assertEqual(set(result), {"restart_requested", "attempt_epoch", "container_id"})
        request.assert_awaited_once()

    async def test_busy_request_is_rejected_without_wait_queue(self):
        self.entry.options = {"frigate_restart_enabled": True}
        async with self.coordinator.restart_lock:
            with patch(
                "custom_components.service_route_sync.frigate_coordinator.request_frigate_restart",
                AsyncMock(),
            ) as request:
                with self.assertRaises(GatewayTransportError):
                    await self.coordinator.async_request_restart()
                request.assert_not_awaited()

    async def test_rebinding_during_status_read_does_not_publish_old_peer(self):
        async def rebind(config):
            self.entry.options["frigate_actor_host"] = "172.20.1.13"
            return self.status

        with patch(
            "custom_components.service_route_sync.frigate_coordinator.read_frigate_status",
            side_effect=rebind,
        ):
            await self.coordinator.async_refresh()
        self.assertFalse(self.coordinator.last_update_success)
        self.assertIsNone(self.coordinator.data)

    async def test_shutdown_blocks_subsequent_restart(self):
        self.entry.options["frigate_restart_enabled"] = True
        await self.coordinator.async_shutdown()
        with patch(
            "custom_components.service_route_sync.frigate_coordinator.request_frigate_restart",
            AsyncMock(),
        ) as request:
            with self.assertRaises(GatewayTransportError):
                await self.coordinator.async_request_restart()
        request.assert_not_awaited()
