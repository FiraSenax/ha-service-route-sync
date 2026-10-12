from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
import bootstrap  # noqa: F401

from service_route_sync.backends.windows_ssh import WindowsSSHConfig, read_windows
from service_route_sync.windows_cursor import AlertCursor


class WindowsTransportTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return WindowsSSHConfig("172.20.11.10", Path("/test/credentials"))

    def test_fixed_account_command_and_no_forwarding(self):
        args = self.config().arguments()
        self.assertEqual(args[-2:], ("ha-windows@172.20.11.10", "service-route-sync windows v1"))
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("IdentityAgent=none", args)
        self.assertIn("ClearAllForwardings=yes", args)

    async def test_only_cursor_sent_and_response_validated(self):
        response = {"schema": 1, "cursor": {"inode": 1, "offset": 20}, "events": [],
                    "agent": None, "heartbeat_ok": False, "rotated": False,
                    "malformed_records": 0, "partial_line": False}
        with patch("service_route_sync.backends.windows_ssh.read_gateway", AsyncMock(return_value=response)) as transport:
            batch = await read_windows(self.config(), AlertCursor(1, 10))
        self.assertEqual(batch.cursor.offset, 20)
        self.assertEqual(transport.call_args.kwargs["input_data"], b'{"schema":1,"cursor":{"inode":1,"offset":10}}')
        self.assertEqual(transport.call_args.kwargs["timeout"], 12)
