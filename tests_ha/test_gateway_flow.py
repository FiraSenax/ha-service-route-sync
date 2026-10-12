"""Observer setup validates trust and cannot overwrite active credentials."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from tests_ha.test_network_flow import FlowHarness
from tests_ha.test_gateway_credentials import PUBLIC
from tests_ha.test_gateway_runtime import observation


class GatewayFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hass = HomeAssistant(self.directory.name)
        self.addAsyncCleanup(self.hass.async_stop, force=True)
        self.flow = FlowHarness()
        self.flow.hass = self.hass
        self.flow.settings.update(
            gateway_observer_host="192.168.1.1", gateway_observer_key_id="b" * 32
        )

    async def test_invalid_trust_or_interfaces_do_not_attempt_connections(self):
        with patch(
            "custom_components.service_route_sync.config_flow.read_gateway",
            new_callable=AsyncMock,
        ) as read:
            data = {
                "gateway_host_public_key": PUBLIC,
                "gateway_primary_interface": "ppp0",
                "gateway_secondary_interface": "eth1",
            }
            for override in (
                {"gateway_host_public_key": "SHA256:unverified"},
                {"gateway_primary_interface": "ppp0\n"},
                {"gateway_secondary_interface": "ppp0"},
                {"gateway_primary_interface": "lo"},
                {"gateway_primary_interface": "../../other"},
            ):
                result = await self.flow.async_step_gateway_observer({**data, **override})
                self.assertEqual(result["errors"]["base"], "invalid_observer_configuration")
            read.assert_not_awaited()

    async def test_verified_handshake_completes_without_storing_host_key_in_entry(self):
        with patch(
            "custom_components.service_route_sync.config_flow.read_gateway",
            new_callable=AsyncMock,
        ) as read:
            read.side_effect = lambda _: observation(dt_util.utcnow())
            result = await self.flow.async_step_gateway_observer(
                {
                    "gateway_host_public_key": PUBLIC,
                    "gateway_primary_interface": "ppp0",
                    "gateway_secondary_interface": "eth1",
                }
            )
        self.assertEqual(result["type"], "create_entry")
        self.assertNotIn("gateway_host_public_key", result["data"])
        self.assertEqual(result["data"]["gateway_primary_interface"], "ppp0")

    async def test_reconfigure_creates_new_identity_without_changing_active_files(self):
        active_id = "b" * 32
        active = Path(self.directory.name) / "service_route_sync" / "gateway" / active_id
        active.mkdir(parents=True, mode=0o700)
        (active / "known_hosts").write_text("existing trust\n")
        self.flow.settings["gateway_observer_enabled"] = True
        with patch("custom_components.service_route_sync.config_flow.UniFiBackend") as backend:
            backend.return_value.read_gateways = AsyncMock(
                return_value={"gateway": {"ip": "192.168.1.1"}}
            )
            result = await self.flow.async_step_network(
                {
                    "network_gateway_id": "gateway",
                    "network_primary_label": "DSL",
                    "network_secondary_label": "Starlink",
                    "network_interval_seconds": 10,
                    "gateway_observer_enabled": True,
                    "gateway_observer_reconfigure": True,
                }
            )
        self.assertEqual(result["step_id"], "gateway_observer")
        self.assertNotEqual(self.flow.settings["gateway_observer_key_id"], active_id)
        self.assertEqual((active / "known_hosts").read_text(), "existing trust\n")
