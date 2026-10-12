from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant, Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from custom_components.service_route_sync.lady_services import register_lady_services
from custom_components.service_route_sync.lady_store import LadyCheckpointStore
from custom_components.service_route_sync.lady_history import LadyHistory, journal_path


class ImportServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.entry = SimpleNamespace(
            entry_id="importtest",
            domain="service_route_sync",
            data={},
            options={"lady_enabled": True, "lady_active": False},
            runtime_data=SimpleNamespace(lady=SimpleNamespace(active=False)),
        )
        self.hass.config_entries = SimpleNamespace(async_get_entry=Mock(return_value=self.entry))
        self.hass.auth = SimpleNamespace(
            async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))
        )
        self.checkpoint = {
            "engine": {},
            "cursor": {"day": "2026-10-12", "offset": 10},
            "outbox": [],
            "tracked_port": None,
            "deferred_events": [],
        }
        self.events = [
            {"key": "synthetic", "time": time.time(), "message": "synthetic", "recovery": False}
        ]
        register_lady_services(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def call(self, revision=0, context=True):
        return await self.hass.services.async_call(
            "service_route_sync",
            "import_lady_checkpoint",
            {
                "entry_id": "importtest",
                "checkpoint": self.checkpoint,
                "history_events": self.events,
                "expected_revision": revision,
            },
            blocking=True,
            return_response=True,
            context=Context(user_id="admin") if context else Context(),
        )

    async def test_real_service_import_restore_and_idempotent_retry(self):
        result = await self.call()
        self.assertEqual(result, {"imported": True, "revision": 1})
        fresh = LadyCheckpointStore(self.hass, "importtest")
        restored = await fresh.async_load()
        self.assertEqual(
            {k: v for k, v in restored.items() if not k.startswith("history_")}, self.checkpoint
        )
        history = LadyHistory(
            journal_path(
                self.hass.config.path("service_route_sync", "lady_history"),
                fresh.store.key,
                restored["history_epoch"],
            )
        )
        self.assertEqual(
            history.snapshot(restored["history_revision"], time.time())["counts"], {"synthetic": 1}
        )
        self.assertEqual(await self.call(1), {"imported": False, "revision": 1})
        with self.assertRaises(ServiceValidationError):
            await self.call(0)

    async def test_failed_replacement_leaves_original_history_and_cursor_usable(self):
        await self.call()
        before = await LadyCheckpointStore(self.hass, "importtest").async_load()
        self.checkpoint["cursor"]["offset"] = 20
        self.events.append({"key": "new", "time": time.time(), "message": "new", "recovery": False})
        with patch(
            "custom_components.service_route_sync.lady_store.Store.async_save",
            AsyncMock(side_effect=OSError("synthetic")),
        ):
            with self.assertRaises(ServiceValidationError):
                await self.call(1)
        restored = await LadyCheckpointStore(self.hass, "importtest").async_load()
        self.assertEqual(restored, before)
        history = LadyHistory(
            journal_path(
                self.hass.config.path("service_route_sync", "lady_history"),
                "service_route_sync.lady.importtest",
                restored["history_epoch"],
            )
        )
        self.assertEqual(
            history.snapshot(restored["history_revision"], time.time())["counts"], {"synthetic": 1}
        )
        await self.call(1)
        newest = await LadyCheckpointStore(self.hass, "importtest").async_load()
        self.assertNotEqual(newest["history_epoch"], before["history_epoch"])

    async def test_nonadmin_and_missing_user_cannot_import(self):
        self.hass.auth.async_get_user.return_value = SimpleNamespace(is_admin=False)
        with self.assertRaises(Unauthorized):
            await self.call()
        with self.assertRaises(ServiceValidationError):
            await self.call(context=False)
        self.assertIsNone(await LadyCheckpointStore(self.hass, "importtest").async_load())

    async def test_active_producer_is_not_overwritten(self):
        self.entry.runtime_data.lady.active = True
        with self.assertRaises(ServiceValidationError):
            await self.call()
        self.assertIsNone(await LadyCheckpointStore(self.hass, "importtest").async_load())
