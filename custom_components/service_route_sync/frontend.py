"""Serve bundled card code through HA without publishing private storage."""

import asyncio
from pathlib import Path

from homeassistant.components.http import StaticPathConfig

from .const import DOMAIN

WIFI_CARD_URL = '/service_route_sync/frontend/wifi-history-card.js'


async def register_frontend(hass):
    state = hass.data.setdefault(f'{DOMAIN}_frontend', {'lock': asyncio.Lock(), 'registered': False})
    async with state['lock']:
        if state['registered']:
            return
        await hass.http.async_register_static_paths([
            StaticPathConfig(WIFI_CARD_URL, str(Path(__file__).parent / 'frontend' / 'wifi-history-card.js'),
                             cache_headers=False)
        ])
        state['registered'] = True
