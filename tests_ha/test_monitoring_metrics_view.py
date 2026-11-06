from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.config_entries import ConfigEntryState
from homeassistant.components.http import KEY_HASS_USER
from homeassistant.util import dt as dt_util
from custom_components.service_route_sync.metrics_view import MonitoringMetricsView


class MonitoringViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        now = dt_util.utcnow().timestamp()
        data = {
            "observed_at": now,
            "row": {},
            "engine": {},
            "pending": 0,
            "sources": {
                key: {"ok": False, "observed_at": None}
                for key in ("api", "probe", "windows", "dns_primary", "dns_secondary")
            },
            "history": {"counts": {}, "recent": []},
        }
        self.coordinator = SimpleNamespace(last_update_success=True, metrics_snapshot=data)
        self.entry = SimpleNamespace(
            domain="service_route_sync",
            state=ConfigEntryState.LOADED,
            data={},
            options={"network_metrics_enabled": True, "monitoring_enabled": True},
            runtime_data=SimpleNamespace(
                monitoring=SimpleNamespace(incidents=self.coordinator), network=None
            ),
        )
        self.hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=Mock(return_value=self.entry))
        )
        self.view = MonitoringMetricsView(self.hass)
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
                        entity_id="sensor.monitoring_mode", unique_id="entry_monitoring_mode"
                    )
                ],
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def test_unauthenticated_and_restricted_users_are_denied(self):
        self.assertEqual((await self.view.get({}, "entry")).status, 401)
        self.user.permissions.check_entity.return_value = False
        self.assertEqual((await self.view.get({KEY_HASS_USER: self.user}, "entry")).status, 403)

    async def test_authorized_cached_snapshot_and_failed_source(self):
        response = await self.view.get({KEY_HASS_USER: self.user}, "entry")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("monitoring_pc_state -1.0", response.text)
        self.coordinator.last_update_success = False
        self.assertEqual((await self.view.get({KEY_HASS_USER: self.user}, "entry")).status, 503)

    async def test_primary_bandwidth_is_exposed_only_to_administrators(self):
        self.entry.runtime_data.network = SimpleNamespace(
            last_update_success=True,
            data=SimpleNamespace(
                primary=SimpleNamespace(
                    observed_at=dt_util.utcnow(), upload_mbit_s=12.5, download_mbit_s=87.25
                )
            ),
        )
        for admin in (False, True):
            with self.subTest(admin=admin):
                self.user.is_admin = admin
                response = await self.view.get({KEY_HASS_USER: self.user}, "entry")
                self.assertEqual(response.status, 200)
                self.assertNotIn("monitoring_dsl_mbps", response.text)
                if admin:
                    self.assertIn('monitoring_primary_mbps{direction="upload"} 12.5', response.text)
                    self.assertIn(
                        'monitoring_primary_mbps{direction="download"} 87.25', response.text
                    )
                else:
                    self.assertNotIn("monitoring_primary_mbps", response.text)
