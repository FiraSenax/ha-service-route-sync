from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import tempfile
import base64
import struct
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.service_route_sync.config_flow import ConfigFlow
from homeassistant.util import dt as dt_util
from custom_components.service_route_sync.gateway_credentials import same_pinned_gateway


class ImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = ConfigFlow()
        self.entry = SimpleNamespace(
            domain="service_route_sync",
            data={"gateway_observer_host": "172.20.0.1", "gateway_observer_key_id": "2" * 32},
            options={"automatic_apply": False, "services": ["blizzard"]},
        )
        self.update = Mock()
        self.flow.hass = SimpleNamespace(
            config=SimpleNamespace(path=lambda *args: str(Path("/tmp").joinpath(*args))),
            async_add_executor_job=AsyncMock(return_value=True),
            config_entries=SimpleNamespace(
                async_get_entry=Mock(return_value=self.entry), async_update_entry=self.update
            ),
        )
        self.flow.async_abort = lambda **kwargs: kwargs
        self.input = {
            "entry_id": "existing",
            "profiles": [
                {"id": "pc", "name": "PC", "macs": ["02:00:00:00:00:01", "02:00:00:00:00:02"]}
            ],
            "probe_host": "172.20.0.1",
            "probe_key_id": "1" * 32,
        }
        self.raw = {
            "schema": 1,
            "observed_at_ms": int(dt_util.utcnow().timestamp() * 1000),
            "clients": {
                "pc": {
                    "identity_verified": False,
                    "probe_at_ms": None,
                    "probe": None,
                    "routing_at_ms": None,
                    "connections": None,
                }
            },
        }

    async def test_full_group_import_preserves_unrelated_options(self):
        with patch(
            "custom_components.service_route_sync.flows.clients.read_client_probes",
            AsyncMock(return_value=self.raw),
        ):
            result = await self.flow.async_step_import(self.input)
        self.assertEqual(result["reason"], "client_profiles_imported")
        options = self.update.call_args.kwargs["options"]
        self.assertEqual(options["client_profiles"], self.input["profiles"])
        self.assertEqual(options["services"], ["blizzard"])
        self.assertFalse(options["automatic_apply"])
        self.assertTrue(options["client_probe_enabled"])

    async def test_failed_handshake_never_saves_options(self):
        self.raw["clients"] = {}
        with patch(
            "custom_components.service_route_sync.flows.clients.read_client_probes",
            AsyncMock(return_value=self.raw),
        ):
            result = await self.flow.async_step_import(self.input)
        self.assertEqual(result["reason"], "invalid_client_import")
        self.update.assert_not_called()

    async def test_router_mismatch_and_unknown_schema_never_save(self):
        for data in [
            {**self.input, "probe_host": "not-an-ip"},
            {**self.input, "extra": "private"},
            {**self.input, "entry_id": []},
        ]:
            self.assertEqual(
                (await self.flow.async_step_import(data))["reason"], "invalid_client_import"
            )
        self.update.assert_not_called()

    async def test_mismatched_host_pins_never_save_or_contact_gateway(self):
        self.flow.hass.async_add_executor_job.return_value = False
        with patch(
            "custom_components.service_route_sync.flows.clients.read_client_probes", AsyncMock()
        ) as reader:
            result = await self.flow.async_step_import(self.input)
        self.assertEqual(result["reason"], "invalid_client_import")
        self.update.assert_not_called()
        reader.assert_not_awaited()

    async def test_reconfigure_uses_existing_gateway_and_entry(self):
        self.entry.entry_id = "existing"
        self.flow.context = {"entry_id": "existing"}
        self.flow.async_step_import = AsyncMock(return_value={"reason": "client_profiles_imported"})
        result = await self.flow.async_step_reconfigure(
            {
                "profiles": self.input["profiles"],
                "probe_key_id": self.input["probe_key_id"],
                "probe_host": self.input["probe_host"],
            }
        )
        self.assertEqual(result["reason"], "client_profiles_imported")
        self.flow.async_step_import.assert_awaited_once_with(self.input)


class PinTests(unittest.TestCase):
    def test_gateway_identity_depends_on_public_key_not_address(self):
        def key(byte):
            raw = (
                struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes([byte]) * 32
            )
            return "ssh-ed25519 " + base64.b64encode(raw).decode()

        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first"
            second = Path(directory) / "second"
            first.write_text("172.20.0.1 " + key(1) + "\n")
            second.write_text("192.0.2.1 " + key(1) + "\n")
            self.assertTrue(same_pinned_gateway(first, second))
            second.write_text("192.0.2.1 " + key(2) + "\n")
            self.assertFalse(same_pinned_gateway(first, second))


if __name__ == "__main__":
    unittest.main()
