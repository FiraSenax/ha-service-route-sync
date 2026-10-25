"""IPv6 options affect no unrelated HA integration settings."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.service_route_sync.config_flow import OptionsFlow
from tests_ha.test_network_flow import FlowHarness


class IPv6FlowTests(unittest.IsolatedAsyncioTestCase):
    def test_unrelated_options_preserve_reviewed_route_automation(self):
        base = {
            "services": ["blizzard"],
            "bindings": {"blizzard": "route"},
            "unifi_tls_pin_sha256": "a" * 64,
            "automatic_apply": True,
        }
        flow = SimpleNamespace(
            defaults=base,
            settings={**base, "ipv6_actor_active": True},
            async_create_entry=lambda **kwargs: kwargs,
        )
        result = OptionsFlow.finish(flow)
        self.assertTrue(result["data"]["automatic_apply"])
        flow.settings["bindings"] = {"blizzard": "different-route"}
        result = OptionsFlow.finish(flow)
        self.assertFalse(result["data"]["automatic_apply"])

    async def test_disabled_scope_preserves_other_options(self):
        flow = FlowHarness()
        flow.defaults.update(
            {
                f"ipv6_{name}_entity": f"input_boolean.custom_{name}"
                for name in ("enabled", "dry_run", "nat66", "sab")
            }
        )
        flow.defaults["ipv6_override_entity"] = "input_select.custom_wan"
        flow.defaults.update(
            gateway_observer_enabled=True,
            gateway_observer_host="192.0.2.1",
            wifi_history_enabled=True,
            monitoring_active=True,
            ipv6_actor_enabled=True,
        )
        result = await OptionsFlow.async_step_ipv6_policy(
            flow,
            {
                "ipv6_actor_enabled": False,
                "ipv6_actor_active": False,
            },
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertFalse(result["data"]["ipv6_actor_enabled"])
        self.assertFalse(result["data"]["ipv6_actor_active"])
        self.assertTrue(result["data"]["wifi_history_enabled"])
        self.assertTrue(result["data"]["monitoring_active"])

    async def test_new_key_starts_in_inspection_mode(self):
        flow = FlowHarness()
        flow.defaults.update(
            {
                f"ipv6_{name}_entity": f"input_boolean.custom_{name}"
                for name in ("enabled", "dry_run", "nat66", "sab")
            }
        )
        flow.defaults["ipv6_override_entity"] = "input_select.custom_wan"
        flow.defaults.update(gateway_observer_enabled=True, gateway_observer_host="192.0.2.1")
        with patch(
            "custom_components.service_route_sync.config_flow.prepare_ipv6_actor_key",
            new=AsyncMock(return_value="ssh-ed25519 test"),
        ):
            result = await OptionsFlow.async_step_ipv6_policy(
                flow,
                {
                    "ipv6_actor_enabled": True,
                    "ipv6_actor_active": False,
                },
            )
        self.assertEqual(result["step_id"], "ipv6_actor")
        self.assertFalse(flow.settings["ipv6_actor_active"])
        self.assertEqual(flow.settings["ipv6_actor_host"], "192.0.2.1")
        self.assertEqual(len(flow.settings["ipv6_actor_key_id"]), 32)
        self.assertTrue(flow._ipv6_only)

    async def test_writer_requires_stopped_legacy_controller(self):
        flow = FlowHarness()
        flow.defaults.update(
            {
                f"ipv6_{name}_entity": f"input_boolean.custom_{name}"
                for name in ("enabled", "dry_run", "nat66", "sab")
            }
        )
        flow.defaults["ipv6_override_entity"] = "input_select.custom_wan"
        flow.defaults.update(
            gateway_observer_enabled=True,
            gateway_observer_host="192.0.2.1",
            ipv6_actor_enabled=True,
            ipv6_actor_key_id="a" * 32,
            ipv6_actor_host="192.0.2.1",
        )
        result = await OptionsFlow.async_step_ipv6_policy(
            flow,
            {
                "ipv6_actor_enabled": True,
                "ipv6_actor_active": True,
            },
        )
        self.assertEqual(result["errors"]["base"], "ipv6_legacy_writer_running")

    async def test_route_tls_fingerprint_is_verified_before_saving(self):
        flow = FlowHarness()
        flow.defaults.update(
            {
                f"ipv6_{name}_entity": f"input_boolean.custom_{name}"
                for name in ("enabled", "dry_run", "nat66", "sab")
            }
        )
        flow.defaults["ipv6_override_entity"] = "input_select.custom_wan"
        flow.defaults.update(bindings={"blizzard": "route-id"}, automatic_apply=False)
        backend = SimpleNamespace(
            read_routes=AsyncMock(return_value={"route-id": {"_id": "route-id"}}),
            async_close=AsyncMock(),
        )
        with patch(
            "custom_components.service_route_sync.config_flow.UniFiBackend", return_value=backend
        ) as factory:
            result = await OptionsFlow.async_step_route_tls(
                flow, {"unifi_tls_pin_sha256": ":".join(["61"] * 32)}
            )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["unifi_tls_pin_sha256"], "61" * 32)
        self.assertFalse(result["data"]["automatic_apply"])
        self.assertEqual(factory.call_args.args[1]["unifi_tls_pin_sha256"], "61" * 32)
        backend.read_routes.assert_awaited_once()
        backend.async_close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
