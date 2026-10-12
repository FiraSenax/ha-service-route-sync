import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.service_route_sync.frontend import register_frontend, WIFI_CARD_URL


class FrontendTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_registration_serves_only_exact_bundled_file(self):
        hass = SimpleNamespace(data={}, http=SimpleNamespace(async_register_static_paths=AsyncMock()))
        await asyncio.gather(register_frontend(hass), register_frontend(hass))
        hass.http.async_register_static_paths.assert_awaited_once()
        configs = hass.http.async_register_static_paths.call_args.args[0]
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].url_path, WIFI_CARD_URL)
        self.assertEqual(Path(configs[0].path).name, 'wifi-history-card.js')
        self.assertTrue(Path(configs[0].path).is_file())
        self.assertFalse(configs[0].cache_headers)

    async def test_failed_registration_can_retry(self):
        hass = SimpleNamespace(data={}, http=SimpleNamespace(async_register_static_paths=AsyncMock(side_effect=OSError('synthetic'))))
        with self.assertRaises(OSError):
            await register_frontend(hass)
        hass.http.async_register_static_paths.side_effect = None
        await register_frontend(hass)
        self.assertEqual(hass.http.async_register_static_paths.await_count, 2)
