from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
import bootstrap  # noqa: F401
from service_route_sync.backends.client_probe_ssh import ClientProbeSSHConfig, read_client_probes


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_command_preserves_pinned_transport_policy(self):
        cfg = ClientProbeSSHConfig("172.20.0.1", Path("/tmp/example-client-identity"))
        args = cfg.arguments()
        self.assertEqual(args[-1], "service-route-sync clients v1")
        for value in [
            "StrictHostKeyChecking=yes",
            "IdentityAgent=none",
            "ProxyCommand=none",
            "PasswordAuthentication=no",
            "ClearAllForwardings=yes",
        ]:
            self.assertIn(value, args)
        self.assertIn(str(cfg.private_key), args)
        with patch(
            "service_route_sync.backends.client_probe_ssh.read_gateway",
            AsyncMock(return_value={"schema": 1}),
        ) as reader:
            self.assertEqual(await read_client_probes(cfg), {"schema": 1})
        reader.assert_awaited_once_with(cfg, timeout=62)


if __name__ == "__main__":
    unittest.main()
