from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant, Context
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import Unauthorized, ServiceValidationError
from custom_components.service_route_sync.frigate.frigate_services import register_frigate_services
from custom_components.service_route_sync.backends.gateway_ssh import GatewayTransportError


class FrigateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.actor = SimpleNamespace(
            restart_enabled=True,
            async_request_restart=AsyncMock(return_value={"restart_requested": True}),
        )
        self.entry = SimpleNamespace(
            domain="service_route_sync",
            state=ConfigEntryState.LOADED,
            runtime_data=SimpleNamespace(frigate_actor=self.actor),
        )
        self.hass.config_entries = SimpleNamespace(async_get_entry=Mock(return_value=self.entry))
        self.hass.auth = SimpleNamespace(
            async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))
        )
        register_frigate_services(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def call(self, context=None):
        return await self.hass.services.async_call(
            "service_route_sync",
            "restart_frigate",
            {"entry_id": "entry"},
            blocking=True,
            return_response=True,
            context=context or Context(user_id="admin"),
        )

    async def test_nonadmin_is_rejected_before_actor(self):
        for user in (None, SimpleNamespace(is_admin=False)):
            self.hass.auth.async_get_user.return_value = user
            with self.assertRaises(Unauthorized):
                await self.call()
        self.actor.async_request_restart.assert_not_awaited()

    async def test_admin_and_system_automation_can_request_enabled_actor(self):
        self.assertEqual(await self.call(), {"restart_requested": True})
        self.assertEqual(await self.call(Context()), {"restart_requested": True})
        self.assertEqual(self.actor.async_request_restart.await_count, 2)

    async def test_observation_mode_never_invokes_actor(self):
        for value in (False, None, 1, "on"):
            self.actor.restart_enabled = value
            with self.assertRaises(ServiceValidationError):
                await self.call()
        self.actor.async_request_restart.assert_not_awaited()

    async def test_missing_wrong_domain_or_unloaded_actor_fails_closed(self):
        for entry in (
            None,
            SimpleNamespace(domain="other", runtime_data=self.entry.runtime_data),
            SimpleNamespace(domain="service_route_sync", runtime_data=None),
            SimpleNamespace(
                domain="service_route_sync",
                state=ConfigEntryState.NOT_LOADED,
                runtime_data=self.entry.runtime_data,
            ),
        ):
            self.hass.config_entries.async_get_entry.return_value = entry
            with self.assertRaises(ServiceValidationError):
                await self.call()
        self.actor.async_request_restart.assert_not_awaited()

    async def test_transport_errors_do_not_leak_credentials_or_paths(self):
        self.actor.async_request_restart.side_effect = GatewayTransportError(
            "private credential path"
        )
        with self.assertRaises(ServiceValidationError) as error:
            await self.call()
        self.assertNotIn("private credential path", str(error.exception))
