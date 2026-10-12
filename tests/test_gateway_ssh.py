"""Trust checks, output limits, cancellation and secret-safe error handling."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import bootstrap  # noqa: F401
from service_route_sync.backends.gateway_ssh import (
    GatewaySSHConfig,
    GatewayTransportError,
    read_gateway,
)


def reader(value=b""):
    stream = asyncio.StreamReader()
    stream.feed_data(value)
    stream.feed_eof()
    return stream


class GatewaySSHTests(unittest.IsolatedAsyncioTestCase):
    def config(self):
        return GatewaySSHConfig("192.168.1.1", Path("/config/service_route_sync/example"))

    def process(self, output=b'{"schema":1}', error=b"", code=0):
        return Mock(
            stdout=reader(output),
            stderr=reader(error),
            returncode=code,
            wait=AsyncMock(return_value=code),
        )

    async def test_fixed_command_strict_trust_and_no_agent(self):
        process = self.process()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            self.assertEqual(await read_gateway(self.config()), {"schema": 1})
            args = spawn.call_args.args
            self.assertEqual(args[-1], "service-route-sync observe v1")
            for option in (
                "StrictHostKeyChecking=yes",
                "BatchMode=yes",
                "IdentityAgent=none",
                "ProxyCommand=none",
                "UpdateHostKeys=no",
            ):
                self.assertIn(option, args)
            self.assertNotIn("SSH_AUTH_SOCK", spawn.call_args.kwargs["env"])

    async def test_bounded_input_is_sent_on_stdin_not_argv(self):
        process = self.process()
        process.stdin = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
        frame = b'{"schema":1,"addresses":{"pc":"172.20.1.10"}}'
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            await read_gateway(self.config(), input_data=frame)
        process.stdin.write.assert_called_once_with(frame)
        process.stdin.close.assert_called_once()
        self.assertNotIn(frame.decode(), spawn.call_args.args)
        self.assertEqual(spawn.call_args.kwargs["stdin"], asyncio.subprocess.PIPE)

    async def test_oversized_input_rejected_before_ssh(self):
        with patch("asyncio.create_subprocess_exec", AsyncMock()) as spawn:
            with self.assertRaises(GatewayTransportError):
                await read_gateway(self.config(), input_data=b"x" * 16385)
        spawn.assert_not_awaited()

    async def test_failed_ssh_never_exposes_stderr(self):
        process = self.process(error=b"private-example", code=255)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            with self.assertRaises(GatewayTransportError) as error:
                await read_gateway(self.config())
        self.assertNotIn("private-example", str(error.exception))

    async def test_duplicate_keys_and_invalid_json_are_rejected(self):
        for value in (b'{"schema":1,"schema":2}', b"private-example", b"\xff"):
            with patch(
                "asyncio.create_subprocess_exec", AsyncMock(return_value=self.process(value))
            ):
                with self.assertRaises(GatewayTransportError):
                    await read_gateway(self.config())

    async def test_output_limits_fail_closed(self):
        for process in (self.process(b"a" * 65537), self.process(error=b"a" * 4097)):
            with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
                with self.assertRaises(GatewayTransportError):
                    await read_gateway(self.config())

    async def test_timeout_stops_child(self):
        process = self.process()
        process.stdout = asyncio.StreamReader()
        process.returncode = None

        def terminate():
            process.returncode = 0

        process.terminate.side_effect = terminate
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            with self.assertRaises(GatewayTransportError):
                await read_gateway(self.config(), timeout=0.01)
        process.terminate.assert_called_once()

    async def test_cancellation_propagates_and_stops_child(self):
        process = self.process()
        process.stdout = asyncio.StreamReader()
        process.returncode = None
        process.terminate.side_effect = lambda: setattr(process, "returncode", 0)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            task = asyncio.create_task(read_gateway(self.config()))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        process.terminate.assert_called_once()

    def test_host_or_port_cannot_inject_ssh_options(self):
        for host in (
            "-oProxyCommand=bad",
            "https://example.com",
            "127.0.0.1",
            "0.0.0.0",
            "ff02::1",
            "fe80::1%eth0",
        ):
            with self.assertRaises(ValueError):
                GatewaySSHConfig(host, Path("/config/example"))
        for port in (True, 0, 65536, "22"):
            with self.assertRaises(ValueError):
                GatewaySSHConfig("192.168.1.1", Path("/config/example"), port)

    def test_unsafe_or_symlinked_trust_material_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o700)
            config = GatewaySSHConfig("192.168.1.1", path)
            for file in (config.private_key, config.known_hosts):
                file.write_text("test")
                file.chmod(0o600)
            config.validate_files()
            config.private_key.chmod(0o644)
            with self.assertRaises(GatewayTransportError):
                config.validate_files()
            config.private_key.unlink()
            config.private_key.symlink_to(config.known_hosts)
            with self.assertRaises(GatewayTransportError):
                config.validate_files()
