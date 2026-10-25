"""The writer profile cannot be confused with the observer or accept loose JSON."""

import tempfile
import unittest
from pathlib import Path

from custom_components.service_route_sync.backends.gateway_ssh import GatewayTransportError
from custom_components.service_route_sync.backends.ipv6_actor_ssh import (
    IPv6ActorSSHConfig,
    parse_actor_response,
)


class IPv6ActorTransportTests(unittest.TestCase):
    def test_dedicated_key_fixed_command_and_pinned_host(self):
        with tempfile.TemporaryDirectory() as directory:
            config = IPv6ActorSSHConfig("172.20.0.1", Path(directory))
            args = config.arguments()
            self.assertEqual(config.private_key.name, "ipv6_actor_ed25519")
            self.assertEqual(args[-1], "service-route-sync ipv6 v1")
            self.assertIn("StrictHostKeyChecking=yes", args)
            self.assertIn("IdentitiesOnly=yes", args)

    def test_inspect_response_is_closed(self):
        valid = {
            "schema": 1,
            "operation": "inspect",
            "mode": "primary",
            "writable": False,
            "owned_mode": "primary",
            "repairable": True,
            "blank": False,
            "healthy": True,
            "digest": "a" * 64,
            "delegation_ready": True,
            "primary_default": True,
            "secondary_default": True,
            "review_required": False,
        }
        parsed = parse_actor_response(valid, "inspect")
        self.assertEqual(parsed.mode, "primary")
        for changed in (
            {**valid, "public_prefix": "2001:db8::/56"},
            {**valid, "healthy": False},
            {**valid, "digest": "secret"},
            {**valid, "primary_default": 1},
            {**valid, "writable": 1},
            {**valid, "owned_mode": "secondary"},
            {**valid, "blank": True},
        ):
            with self.subTest(changed=changed), self.assertRaises(GatewayTransportError):
                parse_actor_response(changed, "inspect")

    def test_apply_response_is_closed(self):
        valid = {
            "schema": 1,
            "operation": "apply",
            "mode": "secondary",
            "applied": True,
            "digest": "b" * 64,
            "review_required": False,
        }
        self.assertTrue(parse_actor_response(valid, "apply").applied)
        for changed in (
            {**valid, "mode": "unknown"},
            {**valid, "review_required": True},
            {**valid, "applied": 1},
            {**valid, "error_code": "secret"},
        ):
            with self.subTest(changed=changed), self.assertRaises(GatewayTransportError):
                parse_actor_response(changed, "apply")
