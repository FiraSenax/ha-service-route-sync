"""Gateway boundary tests use synthetic data; never invoke real root tools."""

import asyncio
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location(
    "gateway_observer", Path(__file__).resolve().parents[1] / "gateway/observer.py"
)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


def configuration():
    return {
        "schema": 1,
        "primary_interface": "ppp0",
        "secondary_interface": "eth1",
        "primary_table": 201,
        "secondary_table": 202,
        "delegated_prefix_length": 56,
    }


class ObserverTests(unittest.IsolatedAsyncioTestCase):
    def test_unrecognized_command_cannot_reach_config_or_tools(self):
        for command in ("", "service-route-sync observe v1; bad", "service-route-sync apply v1"):
            with (
                patch.dict("os.environ", {"SSH_ORIGINAL_COMMAND": command}),
                patch("sys.stdout", new_callable=io.StringIO) as output,
                patch.object(observer, "CONFIG_PATH") as path,
            ):
                self.assertEqual(observer.main(), 2)
                path.open.assert_not_called()
                self.assertEqual(json.loads(output.getvalue())["error_code"], "command_denied")

    def test_config_cannot_inject_a_tool_argument(self):
        for key, value in [
            ("primary_interface", "ppp0;bad"),
            ("primary_interface", "-o"),
            ("primary_interface", "lo"),
            ("primary_table", True),
            ("primary_table", "201"),
            ("secondary_table", 201),
            ("delegated_prefix_length", True),
        ]:
            data = configuration()
            data[key] = value
            with self.assertRaises(observer.ObservationError):
                observer.validate_config(data)
        self.assertEqual(observer.validate_config(configuration()), configuration())

    def test_current_delegation_is_strict_and_independent_of_logs(self):
        row = {
            "ipv6": {
                "dhcp6PDRequestEnabled": True,
                "dhcp6PDStatus": [{"network": "2001:4860::/56"}],
            },
            "pppoe": {"password": "private-example"},
        }
        self.assertEqual(observer.delegation(row, 56), (True, "2001:4860::/56"))
        for prefix in ("fd00::/56", "::/0", "2001:4860::/64", "2001:4860::1/56", None):
            row["ipv6"]["dhcp6PDStatus"] = [{"network": prefix}]
            self.assertEqual(observer.delegation(row, 56), (False, None))
        row["ipv6"]["dhcp6PDStatus"] = []
        self.assertEqual(observer.delegation(row, 56), (True, None))
        row["ipv6"].pop("dhcp6PDStatus")
        self.assertEqual(observer.delegation(row, 56), (False, None))

    async def test_local_route_failure_never_runs_a_ping(self):
        with (
            patch.object(observer, "read_json", AsyncMock(return_value=None)),
            patch.object(observer, "run", AsyncMock()) as run,
        ):
            self.assertIsNone(await observer.probe_target(6, "2606:4700:4700::1111", "eth1", [123]))
            run.assert_not_awaited()

    def test_address_lifetimes_require_valid_kernel_evidence(self):
        address = {
            "family": "inet6",
            "scope": "global",
            "local": "2001:4860::1",
            "valid_life_time": 300,
            "preferred_life_time": 100,
        }
        rows = [{"ifname": "ppp0", "addr_info": [address]}]
        self.assertTrue(observer.has_global_address(rows, "ppp0"))
        for value in (None, True, -1, "300", {}, 4294967296):
            address["preferred_life_time"] = value
            self.assertIsNone(observer.has_global_address(rows, "ppp0"))
        address["preferred_life_time"] = 0
        self.assertFalse(observer.has_global_address(rows, "ppp0"))
        address["preferred_life_time"] = "forever"
        self.assertTrue(observer.has_global_address(rows, "ppp0"))

    async def test_existing_mark_is_used_only_after_matching_route(self):
        with (
            patch.object(observer, "read_json", AsyncMock(side_effect=[None, [{"dev": "eth1"}]])),
            patch.object(observer, "run", AsyncMock(return_value=(0, b"", b""))) as run,
        ):
            self.assertTrue(await observer.probe_target(6, "2606:4700:4700::1111", "eth1", [123]))
            self.assertEqual(run.call_args.args[0][-2:], ["-m", "123"])

    async def test_command_errors_are_unknown_not_connection_failure(self):
        for code, error, expected in (
            (1, b"invalid argument", None),
            (2, b"", None),
            (1, b"", False),
            (0, b"", True),
        ):
            with (
                patch.object(observer, "packet_route", AsyncMock(return_value=(True, None))),
                patch.object(observer, "run", AsyncMock(return_value=(code, b"", error))),
            ):
                self.assertIs(
                    await observer.probe_target(6, "2606:4700:4700::1111", "eth1", []), expected
                )

    async def test_multiple_probe_outcomes_are_not_blindly_coerced(self):
        for results, expected in (
            ([False, False], False),
            ([False, None], None),
            ([True, None], True),
        ):
            with patch.object(observer, "probe_target", AsyncMock(side_effect=results)):
                self.assertIs(await observer.probes(6, "eth1", []), expected)

    async def test_output_filters_private_udapi_before_leaving_gateway(self):
        def row(iface):
            return {
                "identification": {"id": iface},
                "status": {"timestamp": 123, "currentEnabled": True, "plugged": True},
                "ipv6": {
                    "dhcp6PDRequestEnabled": True,
                    "dhcp6PDStatus": [{"network": "2001:4860::/56"}],
                },
                "pppoe": {"password": "private-example", "username": "private-user"},
            }

        responses = [
            [row("ppp0"), row("eth1")],
            [],
            [],
            [{"ifname": "ppp0", "addr_info": []}],
            [{"dst": "default", "dev": "ppp0"}],
            [{"ifname": "eth1", "addr_info": []}],
            [{"dst": "default", "dev": "eth1"}],
        ]
        with (
            patch.object(observer, "read_json", AsyncMock(side_effect=responses)),
            patch.object(observer, "probes", AsyncMock(return_value=True)),
        ):
            result = await observer.observe(configuration())
        self.assertNotIn("private-example", json.dumps(result))
        self.assertNotIn("private-user", json.dumps(result))
        self.assertEqual(set(result), {"schema", "observed_at_ms", "interfaces"})
        self.assertEqual([r["interface"] for r in result["interfaces"]], ["ppp0", "eth1"])
        self.assertTrue(all(r["ipv6_default"] for r in result["interfaces"]))

    async def test_local_tool_timeout_stops_child(self):
        process = unittest.mock.Mock(
            stdout=asyncio.StreamReader(),
            stderr=asyncio.StreamReader(),
            returncode=None,
            wait=AsyncMock(),
        )
        process.kill.side_effect = lambda: setattr(process, "returncode", -9)
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            with self.assertRaises(asyncio.TimeoutError):
                await observer.run(["ip"], timeout=0.01)
        process.kill.assert_called_once()

    def test_configuration_file_must_be_owned_private_and_regular(self):
        import stat
        from types import SimpleNamespace

        path = unittest.mock.MagicMock()
        path.parent.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0)
        path.open.return_value.__enter__.return_value.read.return_value = json.dumps(
            configuration()
        ).encode()
        self.assertEqual(observer.load_config(path), configuration())
        for mode, uid in (
            (stat.S_IFLNK | 0o777, 0),
            (stat.S_IFREG | 0o644, 0),
            (stat.S_IFREG | 0o600, 1000),
        ):
            path.lstat.return_value = SimpleNamespace(st_mode=mode, st_uid=uid)
            with self.assertRaises(observer.ObservationError):
                observer.load_config(path)
