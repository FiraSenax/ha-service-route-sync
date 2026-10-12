from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests_ha import test_network_runtime as base
from custom_components.service_route_sync.client_probe_coordinator import ClientProbeCoordinator
from custom_components.service_route_sync.backends.gateway_ssh import GatewayTransportError
from homeassistant.util import dt as dt_util
from homeassistant.config_entries import ConfigEntry, ConfigEntryState


class ProbeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = base.NetworkRuntimeTests.asyncTearDown

    async def asyncSetUp(self):
        await base.NetworkRuntimeTests.asyncSetUp(self)
        await self.coordinator.async_shutdown()
        self.entry = ConfigEntry(
            data={
                "client_profiles": [{"id": "pc", "macs": ["02:00:00:00:00:01"]}],
                "client_probe_host": "172.20.0.1",
                "client_probe_key_id": "1" * 32,
            },
            options={},
            domain="service_route_sync",
            title="Test",
            unique_id="parent",
            version=1,
            minor_version=1,
            source="user",
            discovery_keys={},
            subentries_data=[],
            state=ConfigEntryState.SETUP_IN_PROGRESS,
        )
        self.coordinator = ClientProbeCoordinator(self.hass, self.entry)
        self.executor = patch.object(self.hass, "async_add_executor_job", AsyncMock())
        self.executor.start()
        self.addCleanup(self.executor.stop)
        self.reader = AsyncMock(
            return_value={
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
        )
        self.patch = patch(
            "custom_components.service_route_sync.client_probe_coordinator.read_client_probes",
            self.reader,
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_identity_unknown_is_not_probe_success(self):
        await self.coordinator.async_config_entry_first_refresh()
        self.assertFalse(self.coordinator.data.identity_verified["pc"])
        self.assertEqual(len(self.coordinator.data.probes), 0)

    async def test_transport_failure_preserves_data_and_recovers(self):
        await self.coordinator.async_config_entry_first_refresh()
        previous = self.coordinator.data
        self.reader.side_effect = GatewayTransportError("private")
        await self.coordinator.async_refresh()
        self.assertFalse(self.coordinator.last_update_success)
        self.assertIs(self.coordinator.data, previous)
        self.reader.side_effect = None
        await self.coordinator.async_refresh()
        self.assertTrue(self.coordinator.last_update_success)

    async def test_fresh_selected_client_ip_is_forwarded_and_failed_source_is_not(self):
        data = SimpleNamespace(
            observed_at=dt_util.utcnow(),
            clients={"pc": SimpleNamespace(online=True, ipv4_address="172.20.1.10")},
        )
        self.coordinator.clients = SimpleNamespace(data=data, last_update_success=True)
        await self.coordinator._async_update_data()
        self.assertEqual(self.reader.call_args.kwargs["addresses"], {"pc": "172.20.1.10"})
        self.coordinator.clients.last_update_success = False
        await self.coordinator._async_update_data()
        self.assertEqual(self.reader.call_args.kwargs["addresses"], {"pc": None})


if __name__ == "__main__":
    unittest.main()
