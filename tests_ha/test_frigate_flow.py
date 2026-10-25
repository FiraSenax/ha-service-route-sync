from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import base64
import struct
import tempfile
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_ha import CONNECTION
from tests_ha.test_wifi_flow import Harness
from custom_components.service_route_sync.frigate_flow import discover_frigate_scopes


class FrigateFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_ignores_symlinks_and_public_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            root.chmod(0o700)
            directory = root / ("a" * 32)
            directory.mkdir(mode=0o700)
            (directory / "id_ed25519").write_text("private-placeholder")
            (directory / "id_ed25519").chmod(0o600)
            blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + b"a" * 32
            pin = directory / "known_hosts"
            pin.write_text("172.20.1.12 ssh-ed25519 " + base64.b64encode(blob).decode() + "\n")
            pin.chmod(0o600)
            (root / ("b" * 32)).symlink_to(directory, target_is_directory=True)
            self.assertEqual(set(discover_frigate_scopes(root)), {"a" * 32})
            directory.chmod(0o755)
            self.assertEqual(discover_frigate_scopes(root), {})

    async def asyncSetUp(self):
        self.flow = Harness()
        self.flow.entry = SimpleNamespace(
            data={**CONNECTION}, options={"gateway_observer_key_id": "preserved"}
        )
        self.flow.hass = SimpleNamespace(
            config=SimpleNamespace(path=lambda *parts: str(Path("/test") / Path(*parts))),
            async_add_executor_job=AsyncMock(),
        )
        self.identifier = "a" * 32
        self.choices = {self.identifier: {"host": "172.20.1.12", "label": "Verified peer"}}
        self.flow.hass.async_add_executor_job.side_effect = lambda function, *args: (
            self.choices if function.__name__ == "discover_frigate_scopes" else None
        )
        await self.flow.async_step_init()
        self.transport = patch(
            "custom_components.service_route_sync.frigate_flow.read_frigate_status",
            AsyncMock(return_value={}),
        )
        self.read = self.transport.start()
        self.addCleanup(self.transport.stop)

    async def test_new_scope_starts_observing_and_preserves_other_features(self):
        result = await self.flow.async_step_frigate_actor(
            {"frigate_actor_enabled": True, "frigate_actor_key_id": self.identifier}
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertFalse(result["data"]["frigate_restart_enabled"])
        self.assertEqual(result["data"]["gateway_observer_key_id"], "preserved")
        self.read.assert_awaited_once()

    async def test_new_binding_cannot_enable_restart_in_same_step(self):
        result = await self.flow.async_step_frigate_actor(
            {
                "frigate_actor_enabled": True,
                "frigate_actor_key_id": self.identifier,
                "frigate_restart_enabled": True,
            }
        )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"], {"base": "frigate_observe_first"})
        self.read.assert_not_awaited()

    async def test_unknown_scope_and_arbitrary_paths_are_rejected(self):
        for identifier in ("../private", "b" * 32):
            result = await self.flow.async_step_frigate_actor(
                {"frigate_actor_enabled": True, "frigate_actor_key_id": identifier}
            )
            self.assertEqual(result["type"], "form")
            self.assertTrue(result["errors"])
        self.read.assert_not_awaited()

    async def test_disabling_works_without_credentials_or_connection(self):
        result = await self.flow.async_step_frigate_actor({"frigate_actor_enabled": False})
        self.assertEqual(result["type"], "create_entry")
        self.assertFalse(result["data"]["frigate_actor_enabled"])
        self.assertFalse(result["data"]["frigate_restart_enabled"])
        self.flow.hass.async_add_executor_job.assert_not_awaited()
        self.read.assert_not_awaited()

    async def test_existing_binding_can_explicitly_enable_control(self):
        self.flow.defaults.update(
            frigate_actor_key_id=self.identifier, frigate_actor_host="172.20.1.12"
        )
        result = await self.flow.async_step_frigate_actor(
            {
                "frigate_actor_enabled": True,
                "frigate_actor_key_id": self.identifier,
                "frigate_restart_enabled": True,
            }
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertTrue(result["data"]["frigate_restart_enabled"])
