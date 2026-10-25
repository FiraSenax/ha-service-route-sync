"""Upgrade tests preserve data across namespaces and reject ambiguous state."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from homeassistant.core import HomeAssistant
from custom_components.service_route_sync.migration import (
    migrate_settings,
    migrate_storage,
    async_migrate_entry,
)


class UpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.hass = HomeAssistant(self.temp.name)
        self.old = "service_route_sync.lady.entry"
        self.new = "service_route_sync.monitoring.entry"
        (self.root / ".storage").mkdir()
        self.checkpoint = {
            "engine": {"incidents": {}},
            "cursor": {"day": "2026-10-14", "offset": 90000000},
            "outbox": [],
            "tracked_port": None,
            "deferred_events": [],
            "history_revision": 7,
        }
        self.doc = {
            "version": 1,
            "key": self.old,
            "data": {"revision": 7, "checkpoint": self.checkpoint},
        }
        (self.root / ".storage" / self.old).write_text(json.dumps(self.doc))
        self.history = (
            self.root / "service_route_sync/lady_history" / self.old / "observation/history.sqlite"
        )
        self.history.parent.mkdir(parents=True)
        self.history.write_bytes(b"original accepted history")

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def test_idempotent_copy_preserves_cursor_queues_revisions_and_history(self):
        for _ in range(2):
            await self.hass.async_add_executor_job(migrate_storage, self.root, "entry")
        new = json.loads((self.root / ".storage" / self.new).read_text())
        self.assertEqual(new["data"], self.doc["data"])
        self.assertEqual(new["key"], self.new)
        self.assertTrue((self.root / ".storage" / self.old).exists())
        self.assertEqual(
            (
                self.root
                / "service_route_sync/monitoring_history"
                / self.new
                / "observation/history.sqlite"
            ).read_bytes(),
            self.history.read_bytes(),
        )

    async def test_conflicting_state_missing_history_and_symlink_fail_closed(self):
        target = self.root / ".storage" / self.new
        target.write_text("{}")
        with self.assertRaises(ValueError):
            migrate_storage(self.root, "entry")
        target.unlink()
        self.history.unlink()
        with self.assertRaises(ValueError):
            migrate_storage(self.root, "entry")
        self.history.write_bytes(b"original accepted history")
        target.symlink_to(self.root / ".storage" / self.old)
        with self.assertRaises(ValueError):
            migrate_storage(self.root, "entry")

    def test_config_migration_preserves_explicit_profiles_and_own_key_only(self):
        data = {
            "lady_profile": {"id": "custom", "name": "Office"},
            "lady_active": True,
            "unifi_api_key": "own-secret",
            "unifi_entry_id": "foreign",
        }
        result = migrate_settings(data)
        self.assertEqual(result["monitoring_profile"], data["lady_profile"])
        self.assertEqual(result["unifi_api_key"], "own-secret")
        self.assertNotIn("unifi_entry_id", result)
        with self.assertRaises(ValueError):
            migrate_settings({"lady_active": True, "monitoring_active": False})

    async def test_upgrade_preserves_entity_ids_and_disables_old_external_writers(self):
        entry = SimpleNamespace(
            version=1,
            entry_id="entry",
            data={"lady_enabled": True, "unifi_api_key": "own-secret"},
            options={"automatic_apply": True, "ipv6_actor_active": True, "lady_active": True},
        )
        update = Mock()
        self.hass.config_entries = SimpleNamespace(async_update_entry=update)
        entities = Mock()
        devices = Mock()
        row = SimpleNamespace(unique_id="entry_lady_mode", entity_id="sensor.existing")
        device = SimpleNamespace(id="device", identifiers={("service_route_sync", "entry_lady")})
        with (
            patch(
                "custom_components.service_route_sync.migration.er.async_get", return_value=entities
            ),
            patch(
                "custom_components.service_route_sync.migration.er.async_entries_for_config_entry",
                return_value=[row],
            ),
            patch(
                "custom_components.service_route_sync.migration.dr.async_get", return_value=devices
            ),
            patch(
                "custom_components.service_route_sync.migration.dr.async_entries_for_config_entry",
                return_value=[device],
            ),
        ):
            self.assertTrue(await async_migrate_entry(self.hass, entry))
        entities.async_update_entity.assert_called_once_with(
            "sensor.existing", new_unique_id="entry_monitoring_mode"
        )
        values = update.call_args.kwargs
        self.assertEqual(values["version"], 2)
        for key in ["automatic_apply", "ipv6_actor_active", "monitoring_active"]:
            self.assertFalse(values["options"][key])

    async def test_current_installation_preserves_modes_and_future_schema_is_refused(self):
        self.hass.config_entries = SimpleNamespace(async_update_entry=Mock())
        entry = SimpleNamespace(version=2)
        self.assertTrue(await async_migrate_entry(self.hass, entry))
        self.hass.config_entries.async_update_entry.assert_called_once_with(entry, minor_version=1)
        self.assertFalse(await async_migrate_entry(self.hass, SimpleNamespace(version=3)))
