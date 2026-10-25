"""Public configuration works without the author's entries, helpers or profiles."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.service_route_sync import async_setup_entry
from custom_components.service_route_sync.config_flow import fields, validate_ipv6_controls
from custom_components.service_route_sync.const import DEFAULTS
from custom_components.service_route_sync.ipv6_coordinator import IPv6Coordinator
from custom_components.service_route_sync.lady_engine import LadyIncidentEngine
from custom_components.service_route_sync.lady_metrics import render_lady
from custom_components.service_route_sync.backends.unifi import read_controller
from tests_ha import CONNECTION
from tests_ha.test_network_flow import FlowHarness


class PublicConfigurationTests(unittest.IsolatedAsyncioTestCase):
    def test_setup_fields_do_not_require_an_official_unifi_entry(self):
        result = fields(SimpleNamespace(), DEFAULTS)({**CONNECTION, "services": ["custom"]})
        self.assertEqual(result["unifi_host"], CONNECTION["unifi_host"])
        self.assertNotIn("unifi_entry_id", result)
        self.assertNotIn("unifi_username", result)
        self.assertNotIn("unifi_password", result)
        self.assertEqual(result["unifi_api_key"], CONNECTION["unifi_api_key"])

    async def test_missing_own_credentials_requests_reauthentication(self):
        entry = SimpleNamespace(data={"unifi_entry_id": "foreign-entry"}, options={})
        with self.assertRaises(ConfigEntryAuthFailed):
            await async_setup_entry(SimpleNamespace(), entry)

    async def test_flow_probe_closes_session_on_failure(self):
        backend = SimpleNamespace(
            read_devices=AsyncMock(side_effect=TimeoutError), async_close=AsyncMock()
        )
        with self.assertRaises(TimeoutError):
            await read_controller(
                None, CONNECTION, "read_devices", backend_factory=lambda *_: backend
            )
        backend.async_close.assert_awaited_once()

    def test_ipv6_has_no_implicit_helper_fallback(self):
        coordinator = object.__new__(IPv6Coordinator)
        coordinator.settings = {}
        coordinator.hass = SimpleNamespace(
            states=SimpleNamespace(get=lambda _: self.fail("Must not read personal helpers"))
        )
        with self.assertRaises(UpdateFailed):
            coordinator._controls()

    def test_custom_helpers_and_override_labels(self):
        coordinator = object.__new__(IPv6Coordinator)
        coordinator.settings = {
            **{
                f"ipv6_{name}_entity": f"input_boolean.office_{name}"
                for name in ("enabled", "dry_run", "nat66", "sab")
            },
            "ipv6_override_entity": "input_select.internet",
            "ipv6_primary_option": "Fiber",
            "ipv6_secondary_option": "Mobile",
            "ipv6_auto_option": "Automatic",
        }
        validate_ipv6_controls(coordinator.settings)
        coordinator.hass = SimpleNamespace(
            states=SimpleNamespace(
                get=lambda entity: SimpleNamespace(
                    state="Mobile" if entity.startswith("input_select.") else "on"
                )
            )
        )
        controls = coordinator._controls()
        self.assertEqual(controls.override, "secondary")
        self.assertTrue(all((controls.enabled, controls.dry_run, controls.nat66, controls.sab)))
        coordinator.hass.states.get = lambda entity: SimpleNamespace(
            state="Mobile" if entity.startswith("input_select.") else "off"
        )
        controls = coordinator._controls()
        self.assertFalse(any((controls.enabled, controls.dry_run, controls.nat66, controls.sab)))
        coordinator.settings["ipv6_auto_option"] = "Mobile"
        with self.assertRaises(ValueError):
            validate_ipv6_controls(coordinator.settings)

    async def test_arbitrary_profile_name_and_optional_windows_source(self):
        flow = FlowHarness()
        flow.settings["lady_enabled"] = True
        flow.async_step_lady_sources = AsyncMock(return_value={"type": "form"})
        backend = SimpleNamespace(read_clients=AsyncMock(return_value=[]), async_close=AsyncMock())
        with patch(
            "custom_components.service_route_sync.lady_flow.UniFiBackend", return_value=backend
        ):
            await flow.async_step_lady(
                {
                    "lady_profile_id": "office_pc",
                    "lady_profile_name": "Office computer",
                    "lady_manual_mac": "02:00:00:00:00:04",
                    "lady_windows_enabled": False,
                    "lady_notify_service": "test_target",
                }
            )
        self.assertEqual(flow.settings["lady_profile"]["id"], "office_pc")
        self.assertEqual(flow.settings["lady_profile"]["name"], "Office computer")
        self.assertFalse(flow.settings["lady_windows_enabled"])
        self.assertEqual(flow.settings["lady_notify_service"], "test_target")
        flow.async_step_lady_sources.assert_awaited_once()

    def test_incident_engine_uses_configured_home_assistant_timezone(self):
        stamp = datetime(2026, 1, 1, 5, 1, tzinfo=timezone.utc).timestamp()
        for zone, maintenance in ((timezone.utc, True), (timezone(timedelta(hours=-5)), False)):
            engine = LadyIncidentEngine(maintenance_hour=5)
            with (
                patch(
                    "custom_components.service_route_sync.lady_engine.dt_util.get_default_time_zone",
                    return_value=zone,
                ),
                patch.object(engine, "check") as check,
            ):
                engine.wan_dns({}, stamp)
            categories = [call.args[0] for call in check.call_args_list]
            self.assertEqual("Primär-WAN-Testpfad" not in categories, maintenance)

    def test_metrics_use_configured_home_assistant_timezone(self):
        stamp = datetime(2026, 1, 1, 5, 1, tzinfo=timezone.utc).timestamp()
        snapshot = {
            "engine_options": {"maintenance_hour": 5},
            "observed_at": stamp,
            "row": {},
            "engine": {},
            "pending": 0,
            "sources": {
                key: {"ok": False, "observed_at": None}
                for key in ("api", "probe", "windows", "dns253", "dns254")
            },
            "history": {"counts": {}, "recent": []},
        }
        with patch(
            "custom_components.service_route_sync.lady_metrics.dt_util.get_default_time_zone",
            return_value=timezone.utc,
        ):
            self.assertIn("lady_maintenance 1.0", render_lady(snapshot, stamp))
        with patch(
            "custom_components.service_route_sync.lady_metrics.dt_util.get_default_time_zone",
            return_value=timezone(timedelta(hours=-5)),
        ):
            self.assertIn("lady_maintenance 0.0", render_lady(snapshot, stamp))
