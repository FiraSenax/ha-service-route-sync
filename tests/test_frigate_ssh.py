import base64
from pathlib import Path
import os
import struct
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import bootstrap  # noqa: F401

from service_route_sync.backends.frigate_ssh import (
    FrigateStatusSSHConfig,
    FrigateRestartSSHConfig,
    validate_reply,
    read_frigate_status,
    request_frigate_restart,
)
from service_route_sync.backends.gateway_ssh import GatewayTransportError


class FrigateSSHTests(unittest.IsolatedAsyncioTestCase):
    def status(self):
        return {
            "protocol": 1,
            "ok": True,
            "action": "status",
            "container_id": "a" * 64,
            "running": True,
            "started": "2026-10-12T04:00:00Z",
        }

    def test_fixed_commands_private_peer_and_no_forwarding(self):
        status = FrigateStatusSSHConfig("172.20.1.12", Path("/private"))
        restart = FrigateRestartSSHConfig(status.host, status.credential_directory)
        self.assertEqual(status.arguments()[-1], "status-v1")
        self.assertEqual(restart.arguments()[-1], "restart-v1")
        self.assertIn("ClearAllForwardings=yes", status.arguments())
        self.assertIn("HostKeyAlgorithms=ssh-ed25519", status.arguments())
        for host in ["1.1.1.1", "127.0.0.1", "::1", "example.com"]:
            with self.assertRaises(ValueError):
                FrigateStatusSSHConfig(host, Path("/private"))

    def test_host_pin_is_exact_and_hardlinked_private_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            root.chmod(0o700)
            (root / "id_ed25519").write_text("private-placeholder")
            (root / "id_ed25519").chmod(0o600)
            blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + b"a" * 32
            pin = root / "known_hosts"
            pin.write_text("172.20.1.12 ssh-ed25519 " + base64.b64encode(blob).decode() + "\n")
            pin.chmod(0o600)
            config = FrigateStatusSSHConfig("172.20.1.12", root)
            config.validate_files()
            os.link(root / "id_ed25519", root / "alias")
            with self.assertRaises(GatewayTransportError):
                config.validate_files()
            (root / "alias").unlink()
            pin.write_text(pin.read_text().replace("172.20.1.12", "172.20.1.13"))
            with self.assertRaises(GatewayTransportError):
                config.validate_files()

    def test_closed_response_rejects_bool_protocol_extra_fields_and_wrong_action(self):
        for change in (
            {"protocol": True},
            {"running": 1},
            {"secret": "unexpected"},
            {"action": "restart"},
            {"started": "not a timestamp"},
            {"started": "2026-10-12T04:00:00"},
        ):
            with self.assertRaises(GatewayTransportError):
                validate_reply({**self.status(), **change}, "status")

    async def test_scope_and_deadline_separate_observation_from_control(self):
        status = FrigateStatusSSHConfig("172.20.1.12", Path("/private"))
        restart = FrigateRestartSSHConfig(status.host, status.credential_directory)
        with patch(
            "service_route_sync.backends.frigate_ssh.exchange_pinned_command",
            AsyncMock(return_value=self.status()),
        ) as exchange:
            await read_frigate_status(status)
            self.assertEqual(exchange.call_args.kwargs, {"timeout": 15})
            with self.assertRaises(GatewayTransportError):
                await read_frigate_status(restart)
            with self.assertRaises(GatewayTransportError):
                await request_frigate_restart(status)
            self.assertEqual(exchange.await_count, 1)
            exchange.return_value = {
                "protocol": 1,
                "ok": True,
                "action": "restart",
                "container_id": "a" * 64,
                "restart_requested": True,
                "attempt_epoch": 10000,
            }
            await request_frigate_restart(restart)
            self.assertEqual(exchange.call_args.kwargs, {"timeout": 60})
