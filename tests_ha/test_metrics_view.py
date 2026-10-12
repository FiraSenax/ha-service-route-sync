from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.util import dt as dt_util
from homeassistant.config_entries import ConfigEntryState
from homeassistant.components.http import KEY_HASS_USER
from custom_components.service_route_sync.metrics_view import NetworkMetricsView
from custom_components.service_route_sync.client_observation import observe_client
from custom_components.service_route_sync.work_probes import WorkProbe


class MetricsViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_lady_permissions_do_not_widen_gaming_endpoint_scope(self):
        self.user.permissions.check_entity.side_effect = lambda entity, policy: (
            entity == "binary_sensor.pc_connected"
        )
        rows = [
            SimpleNamespace(
                entity_id="binary_sensor.pc_connected", unique_id="entry_client_pc_online"
            ),
            SimpleNamespace(
                entity_id="binary_sensor.lady_connected",
                unique_id="entry_client_ladyofgames_online",
            ),
        ]
        with patch(
            "custom_components.service_route_sync.metrics_view.er.async_entries_for_config_entry",
            return_value=rows,
        ):
            response = await self.view.get({KEY_HASS_USER: self.user}, "entry")
        self.assertEqual(response.status, 200)

    def setUp(self):
        now = dt_util.utcnow()
        mac = "02:00:00:00:00:01"
        obs = observe_client(
            [mac], [{"mac": mac, "last_seen": now.timestamp(), "is_wired": True}], [], now
        )
        clients = SimpleNamespace(
            profiles={"pc": (mac,)},
            data=SimpleNamespace(observed_at=now, clients={"pc": obs}),
            last_update_success=True,
        )
        probes = SimpleNamespace(data=None, last_update_success=False)
        work = SimpleNamespace(
            data={"m365-login": WorkProbe(now, True, 302, True, 0, 0, 0, 0, 0)},
            last_update_success=True,
        )
        self.entry = SimpleNamespace(
            domain="service_route_sync",
            state=ConfigEntryState.LOADED,
            data={},
            options={"network_metrics_enabled": True},
            runtime_data=SimpleNamespace(clients=clients, client_probes=probes, work_probes=work),
        )
        self.hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=Mock(return_value=self.entry))
        )
        self.view = NetworkMetricsView(self.hass)
        self.user = SimpleNamespace(
            permissions=SimpleNamespace(check_entity=Mock(return_value=True))
        )
        self.patches = [
            patch(
                "custom_components.service_route_sync.metrics_view.er.async_get",
                return_value=Mock(),
            ),
            patch(
                "custom_components.service_route_sync.metrics_view.er.async_entries_for_config_entry",
                return_value=[
                    SimpleNamespace(
                        entity_id="binary_sensor.pc_connected", unique_id="entry_client_pc_online"
                    )
                ],
            ),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_authentication_required_and_no_user_denied(self):
        self.assertTrue(self.view.requires_auth)
        self.assertEqual((await self.view.get({}, "entry")).status, 401)

    async def test_restricted_user_denied_without_measurements(self):
        self.user.permissions.check_entity.return_value = False
        result = await self.view.get({KEY_HASS_USER: self.user}, "entry")
        self.assertEqual(result.status, 403)
        self.assertIsNone(result.text)

    async def test_read_permission_exports_correct_metric_contract(self):
        result = await self.view.get({KEY_HASS_USER: self.user}, "entry")
        self.assertEqual(result.status, 200)
        self.assertIn('gaming_pc_state{pc="pc"} -1.0', result.text)
        self.assertIn(
            'work_endpoint_http_status{host="login.microsoftonline.com",target="m365-login",vantage="house"} 302.0',
            result.text,
        )
        self.assertEqual(result.headers["Cache-Control"], "no-store")

    async def test_unloaded_and_disabled_entry_returns_unavailable(self):
        self.entry.state = ConfigEntryState.NOT_LOADED
        self.assertEqual((await self.view.get({KEY_HASS_USER: self.user}, "entry")).status, 503)
        self.entry.state = ConfigEntryState.LOADED
        self.entry.options["network_metrics_enabled"] = False
        self.assertEqual((await self.view.get({KEY_HASS_USER: self.user}, "entry")).status, 503)

    async def test_failed_client_source_omits_cached_online_measurement(self):
        self.entry.runtime_data.clients.last_update_success = False
        result = await self.view.get({KEY_HASS_USER: self.user}, "entry")
        self.assertNotIn("gaming_client_online", result.text)
        self.assertIn('gaming_source_ok{pc="pc",source="unifi"} 0.0', result.text)


if __name__ == "__main__":
    unittest.main()
