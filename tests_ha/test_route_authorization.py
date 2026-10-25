from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized
from custom_components.service_route_sync.switch import RouteSwitch
from custom_components.service_route_sync.button import RouteButton


class RouteAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = SimpleNamespace(
            entry=SimpleNamespace(entry_id="entry", title="Routes"),
            set_automatic=AsyncMock(),
            restore_last=AsyncMock(),
            async_request_refresh=AsyncMock(),
        )
        self.hass = SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock()))

    def entity(self, cls, key, context):
        entity = cls(self.coordinator, key, key)
        entity.hass = self.hass
        entity._context = context
        return entity

    async def test_nonadmin_and_deleted_users_cannot_enable_disable_restore_or_trigger_write_cycle(
        self,
    ):
        for user in [None, SimpleNamespace(is_admin=False)]:
            self.hass.auth.async_get_user.return_value = user
            for cls, key, method in [
                (RouteSwitch, "automatic", "async_turn_on"),
                (RouteSwitch, "automatic", "async_turn_off"),
                (RouteButton, "restore", "async_press"),
                (RouteButton, "refresh", "async_press"),
            ]:
                entity = self.entity(cls, key, Context(user_id="ordinary"))
                with self.assertRaises(Unauthorized):
                    await getattr(entity, method)()
        for method in ["set_automatic", "restore_last", "async_request_refresh"]:
            getattr(self.coordinator, method).assert_not_awaited()

    async def test_administrator_and_trusted_automation_keep_working(self):
        self.hass.auth.async_get_user.return_value = SimpleNamespace(is_admin=True)
        for context in [Context(user_id="admin"), Context()]:
            await self.entity(RouteSwitch, "automatic", context).async_turn_on()
            await self.entity(RouteButton, "restore", context).async_press()
        self.assertEqual(self.coordinator.set_automatic.await_count, 2)
        self.assertEqual(self.coordinator.restore_last.await_count, 2)
