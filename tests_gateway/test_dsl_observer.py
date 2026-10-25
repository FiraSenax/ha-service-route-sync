from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))
from dsl_observer import sample, route_is_dsl


class DSLTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_default_route_fallback_when_dsl_route_missing(self):
        with patch(
            "dsl_observer.run", AsyncMock(return_value=(0, b'[{"dev":"eth1"}]', b""))
        ) as run:
            result = await sample("1.1.1.1", "wan0", "1234")
        self.assertIsNone(result["loss"])
        self.assertEqual(result["error"], "dsl_route_unavailable")
        self.assertEqual(run.await_count, 1)

    async def test_probe_is_interface_and_mark_bound_and_route_rechecked(self):
        output = b"3 packets transmitted, 3 received, 0% packet loss\nrtt min/avg/max/mdev = 1.0/2.0/3.0/0.5 ms\n"
        with patch("dsl_observer.route_is_dsl", AsyncMock(return_value=True)) as route:
            with patch("dsl_observer.run", AsyncMock(return_value=(0, output, b""))) as run:
                result = await sample("8.8.8.8", "wan0", "1234")
        self.assertEqual(result["avg"], 2)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("-I") + 1], "wan0")
        self.assertEqual(args[args.index("-m") + 1], "1234")
        self.assertEqual(route.await_count, 2)

    async def test_route_change_discards_measurements(self):
        with patch("dsl_observer.route_is_dsl", AsyncMock(side_effect=[True, False])):
            with patch("dsl_observer.run", AsyncMock(return_value=(0, b"irrelevant", b""))):
                result = await sample("1.1.1.1", "wan0", "1234")
        self.assertIsNone(result["loss"])
        self.assertEqual(result["error"], "dsl_route_changed")

    async def test_malformed_routing_output_is_unknown(self):
        with patch("dsl_observer.run", AsyncMock(return_value=(0, b"null", b""))):
            self.assertFalse(await route_is_dsl("1.1.1.1", "wan0", "1234"))
