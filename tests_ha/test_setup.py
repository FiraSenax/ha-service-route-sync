"""Feature isolation and backward compatibility at HA entry setup."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_ha import CONNECTION
from homeassistant.exceptions import ConfigEntryNotReady
from custom_components.service_route_sync import async_setup_entry, async_unload_entry
from custom_components.service_route_sync.runtime import RuntimeData


class SetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_frigate_observation_lifecycle_is_independent(self):
        hass, entry = self.environment({"frigate_actor_enabled": True})
        actor = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(),
            async_add_listener=Mock(return_value=Mock()),
            async_shutdown=AsyncMock(),
        )
        with patch(
            "custom_components.service_route_sync.FrigateActorCoordinator", return_value=actor
        ):
            self.assertTrue(await async_setup_entry(hass, entry))
        self.assertIs(entry.runtime_data.frigate_actor, actor)
        self.assertIsNone(entry.runtime_data.clients)
        actor.async_add_listener.assert_called_once()
        self.assertTrue(await async_unload_entry(hass, entry))
        actor.async_shutdown.assert_awaited_once()

    async def test_invalid_frigate_profile_does_not_block_wan(self):
        with patch(
            "custom_components.service_route_sync.FrigateActorCoordinator",
            side_effect=ValueError("synthetic"),
        ):
            result, _, entry, _, network = await self.run_setup(
                {"network_enabled": True, "frigate_actor_enabled": True}
            )
        self.assertTrue(result)
        self.assertIs(entry.runtime_data.network, network)
        self.assertIsNone(entry.runtime_data.frigate_actor)

    async def test_wifi_history_polls_and_unloads_without_gaming_or_cloud(self):
        hass, entry = self.environment({"wifi_history_enabled": True, **CONNECTION})
        wifi = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(),
            async_add_listener=Mock(return_value=Mock()),
            async_shutdown=AsyncMock(),
        )
        with patch("custom_components.service_route_sync.prepare_wifi", return_value=wifi):
            self.assertTrue(await async_setup_entry(hass, entry))
        self.assertIs(entry.runtime_data.wifi, wifi)
        self.assertIsNone(entry.runtime_data.clients)
        self.assertIsNone(entry.runtime_data.routes)
        wifi.async_add_listener.assert_called_once()
        self.assertTrue(await async_unload_entry(hass, entry))
        wifi.async_shutdown.assert_awaited_once()

    async def test_wifi_initial_failure_keeps_retry_without_blocking_wan(self):
        wifi = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(side_effect=ConfigEntryNotReady()),
            async_add_listener=Mock(return_value=Mock()),
            async_shutdown=AsyncMock(),
        )
        with patch("custom_components.service_route_sync.prepare_wifi", return_value=wifi):
            result, _, entry, _, network = await self.run_setup(
                {"wifi_history_enabled": True, "network_enabled": True, **CONNECTION}
            )
        self.assertTrue(result)
        self.assertIs(entry.runtime_data.network, network)
        wifi.async_add_listener.assert_called_once()
        wifi.async_shutdown.assert_not_awaited()

    async def test_lady_source_setup_failure_does_not_block_existing_wan(self):
        with patch(
            "custom_components.service_route_sync.prepare_lady",
            AsyncMock(side_effect=ConfigEntryNotReady()),
        ):
            result, _, entry, _, network = await self.run_setup(
                {"network_enabled": True, "lady_enabled": True}
            )
        self.assertTrue(result)
        self.assertIs(entry.runtime_data.network, network)
        self.assertIsNone(entry.runtime_data.lady)

    async def test_lady_polling_and_shutdown_are_independent_of_gaming(self):
        hass, entry = self.environment({"lady_enabled": True})

        def coordinator():
            return SimpleNamespace(
                async_config_entry_first_refresh=AsyncMock(),
                async_add_listener=Mock(return_value=Mock()),
                async_shutdown=AsyncMock(),
            )

        clients, incidents = coordinator(), coordinator()
        lady = SimpleNamespace(clients=clients, incidents=incidents, active=False)
        with patch(
            "custom_components.service_route_sync.prepare_lady", AsyncMock(return_value=lady)
        ):
            self.assertTrue(await async_setup_entry(hass, entry))
        self.assertIsNone(entry.runtime_data.clients)
        clients.async_add_listener.assert_called_once()
        incidents.async_add_listener.assert_called_once()
        self.assertTrue(await async_unload_entry(hass, entry))
        clients.async_shutdown.assert_awaited_once()
        incidents.async_shutdown.assert_awaited_once()

    def environment(self, data):
        entry = SimpleNamespace(
            data={**CONNECTION, **data},
            options={},
            unique_id="parent",
            entry_id="entry",
            async_on_unload=Mock(),
            add_update_listener=Mock(),
        )
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(
                async_forward_entry_setups=AsyncMock(),
                async_unload_platforms=AsyncMock(return_value=True),
            )
        )
        return hass, entry

    async def run_setup(self, data, route_error=None, network_error=None):
        hass, entry = self.environment(data)
        routes = SimpleNamespace(
            load=AsyncMock(),
            async_config_entry_first_refresh=AsyncMock(side_effect=route_error),
            async_shutdown=AsyncMock(),
        )
        network = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(side_effect=network_error),
            async_shutdown=AsyncMock(),
        )
        with (
            patch("custom_components.service_route_sync.RouteCoordinator", return_value=routes),
            patch("custom_components.service_route_sync.NetworkCoordinator", return_value=network),
        ):
            result = await async_setup_entry(hass, entry)
        return result, hass, entry, routes, network

    async def test_legacy_routes_only_keeps_original_runtime(self):
        result, _, entry, routes, network = await self.run_setup({"services": ["blizzard"]})
        self.assertTrue(result)
        self.assertIs(entry.runtime_data.routes, routes)
        self.assertIsNone(entry.runtime_data.network)
        network.async_config_entry_first_refresh.assert_not_awaited()

    async def test_network_only_does_not_load_feeds_or_route_backups(self):
        _, _, entry, routes, network = await self.run_setup(
            {"services": [], "network_enabled": True}
        )
        self.assertIsNone(entry.runtime_data.routes)
        self.assertIs(entry.runtime_data.network, network)
        routes.load.assert_not_awaited()

    async def test_one_module_outage_does_not_block_the_other(self):
        data = {"services": ["blizzard"], "network_enabled": True}
        for route_error, network_error in (
            (ConfigEntryNotReady(), None),
            (None, ConfigEntryNotReady()),
        ):
            result, hass, entry, _, _ = await self.run_setup(data, route_error, network_error)
            self.assertTrue(result)
            self.assertIsNotNone(entry.runtime_data.routes)
            self.assertIsNotNone(entry.runtime_data.network)
            hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    async def test_all_modules_down_retries_entry_setup(self):
        with self.assertRaises(ConfigEntryNotReady):
            await self.run_setup(
                {"services": ["blizzard"], "network_enabled": True},
                ConfigEntryNotReady(),
                ConfigEntryNotReady(),
            )

    async def test_empty_configuration_does_not_load(self):
        with self.assertRaises(ConfigEntryNotReady):
            await self.run_setup({"services": []})

    async def test_unload_uses_ha_platform_lifecycle(self):
        hass, entry = self.environment({})
        entry.runtime_data = RuntimeData()
        self.assertTrue(await async_unload_entry(hass, entry))
        hass.config_entries.async_unload_platforms.assert_awaited_once()

    async def test_clients_only_can_start_without_cloud_or_wan(self):
        hass, entry = self.environment({"client_profiles": [{"id": "pc", "macs": []}]})
        clients = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(),
            async_add_listener=Mock(return_value=Mock()),
        )
        with patch("custom_components.service_route_sync.ClientCoordinator", return_value=clients):
            self.assertTrue(await async_setup_entry(hass, entry))
        self.assertIs(entry.runtime_data.clients, clients)
        self.assertIsNone(entry.runtime_data.routes)
        self.assertIsNone(entry.runtime_data.network)

    async def test_initial_probe_failure_keeps_native_retry_subscription(self):
        hass, entry = self.environment(
            {"client_profiles": [{"id": "pc", "macs": []}], "client_probe_enabled": True}
        )
        clients = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(),
            async_add_listener=Mock(return_value=Mock()),
        )
        probes = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(side_effect=ConfigEntryNotReady()),
            async_add_listener=Mock(return_value=Mock()),
        )
        with (
            patch("custom_components.service_route_sync.ClientCoordinator", return_value=clients),
            patch(
                "custom_components.service_route_sync.ClientProbeCoordinator", return_value=probes
            ),
        ):
            self.assertTrue(await async_setup_entry(hass, entry))
        probes.async_add_listener.assert_called_once()
        entry.async_on_unload.assert_any_call(probes.async_add_listener.return_value)

    async def test_work_probes_only_start_without_clients_or_cloud(self):
        hass, entry = self.environment({"work_probe_enabled": True})
        work = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(),
            async_add_listener=Mock(return_value=Mock()),
        )
        with patch("custom_components.service_route_sync.WorkProbeCoordinator", return_value=work):
            self.assertTrue(await async_setup_entry(hass, entry))
        self.assertIs(entry.runtime_data.work_probes, work)
        self.assertIsNone(entry.runtime_data.clients)
        work.async_add_listener.assert_called_once()

    async def test_successful_unload_stops_all_collectors(self):
        hass, entry = self.environment({})
        collectors = [SimpleNamespace(async_shutdown=AsyncMock()) for _ in range(6)]
        entry.runtime_data = RuntimeData(*collectors)
        self.assertTrue(await async_unload_entry(hass, entry))
        for collector in collectors:
            collector.async_shutdown.assert_awaited_once()

    async def test_failed_platform_unload_preserves_collectors(self):
        hass, entry = self.environment({})
        hass.config_entries.async_unload_platforms.return_value = False
        clients = SimpleNamespace(async_shutdown=AsyncMock())
        entry.runtime_data = RuntimeData(clients=clients)
        self.assertFalse(await async_unload_entry(hass, entry))
        clients.async_shutdown.assert_not_awaited()

    async def test_platform_setup_failure_stops_started_collector(self):
        hass, entry = self.environment({"client_profiles": [{"id": "pc", "macs": []}]})
        hass.config_entries.async_forward_entry_setups.side_effect = RuntimeError("setup failed")
        clients = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(), async_shutdown=AsyncMock()
        )
        with patch("custom_components.service_route_sync.ClientCoordinator", return_value=clients):
            with self.assertRaises(RuntimeError):
                await async_setup_entry(hass, entry)
        clients.async_shutdown.assert_awaited_once()
