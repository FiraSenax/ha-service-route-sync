import csv
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant, Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from custom_components.service_route_sync.wifi_history import WifiHistory, COLUMNS
from custom_components.service_route_sync.wifi_services import register_wifi_services


class WifiImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        directory = Path(self.temp.name) / "private"
        directory.mkdir(mode=0o700)
        self.history = WifiHistory(directory / "wifi.sqlite")
        self.entry = SimpleNamespace(
            entry_id="entry",
            domain="service_route_sync",
            runtime_data=SimpleNamespace(wifi=SimpleNamespace(history=self.history)),
        )
        self.hass.config_entries = SimpleNamespace(async_get_entry=Mock(return_value=self.entry))
        self.hass.auth = SimpleNamespace(
            async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True))
        )
        temporary = directory / "source.csv"
        with temporary.open("w", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(COLUMNS)
            writer.writerow(["1000"] + ["original"] * 16)
        self.digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        identity = hashlib.sha256(b"entry").hexdigest()
        self.source = directory / f"{identity}.{self.digest}.csv"
        temporary.rename(self.source)
        self.source.chmod(0o600)
        register_wifi_services(self.hass)

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.temp.cleanup()

    async def call(self, context=None):
        return await self.hass.services.async_call(
            "service_route_sync",
            "import_wifi_history",
            {"entry_id": "entry", "sha256": self.digest},
            blocking=True,
            return_response=True,
            context=context or Context(user_id="admin"),
        )

    async def test_real_service_import_is_lossless_and_idempotent(self):
        self.assertEqual(await self.call(), {"imported": True, "rows": 1})
        self.assertEqual(await self.call(), {"imported": False, "rows": 1})
        self.assertEqual(self.history.read_page("legacy")["rows"], [["1000"] + ["original"] * 16])

    async def test_nonadmin_missing_user_and_public_file_are_rejected(self):
        self.hass.auth.async_get_user.return_value = SimpleNamespace(is_admin=False)
        with self.assertRaises(Unauthorized):
            await self.call()
        self.hass.auth.async_get_user.return_value = SimpleNamespace(is_admin=True)
        with self.assertRaises(ServiceValidationError):
            await self.call(Context())
        self.source.chmod(0o644)
        with self.assertRaises(ServiceValidationError):
            await self.call()
        self.assertEqual(self.history.read_page("legacy")["rows"], [])
