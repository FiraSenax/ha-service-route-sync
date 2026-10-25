from datetime import datetime, timedelta, timezone
import unittest
import bootstrap  # noqa: F401
from service_route_sync.probes.work_probes import parse_curl, render_work_probes
from service_route_sync.network.network import NetworkDataError

NOW = datetime(2026, 10, 11, 20, tzinfo=timezone.utc)


class WorkProbeTests(unittest.TestCase):
    def setUp(self):
        self.raw = {
            "http_code": 302,
            "time_namelookup": 0.01,
            "time_connect": 0.03,
            "time_appconnect": 0.08,
            "time_starttransfer": 0.1,
            "time_total": 0.12,
        }

    def test_actual_cumulative_phases_become_exact_durations(self):
        row = parse_curl(self.raw, 0, NOW)
        self.assertTrue(row.transport_ok)
        self.assertTrue(row.http_ok)
        self.assertAlmostEqual(row.tcp_seconds, 0.02)
        self.assertAlmostEqual(row.tls_seconds, 0.05)
        self.assertEqual(row.ttfb_seconds, 0.1)

    def test_failed_transport_has_no_fabricated_durations(self):
        self.raw["http_code"] = 0
        row = parse_curl(self.raw, 6, NOW)
        text = render_work_probes({"m365-login": row}, NOW)
        self.assertIn("work_endpoint_transport_ok", text)
        self.assertNotIn("work_endpoint_dns_seconds", text)

    def test_http_error_does_not_become_transport_error(self):
        self.raw["http_code"] = 401
        row = parse_curl(self.raw, 0, NOW)
        self.assertTrue(row.transport_ok)
        self.assertFalse(row.http_ok)

    def test_invalid_and_nonmonotone_phases_rejected(self):
        for value in [True, "1", float("nan"), -1, 0.2]:
            self.raw["time_namelookup"] = value
            with self.assertRaises(NetworkDataError):
                parse_curl(self.raw, 0, NOW)

    def test_stale_and_future_measurements_emit_only_freshness(self):
        row = parse_curl(self.raw, 0, NOW)
        for stamp in [NOW + timedelta(seconds=150), NOW - timedelta(seconds=1)]:
            text = render_work_probes({"m365-login": row}, stamp)
            self.assertEqual(len(text.splitlines()), 1)
            self.assertIn(" 0.0", text)


if __name__ == "__main__":
    unittest.main()


class ProbeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_user_configuration_proxy_environment_or_unbounded_body(self):
        import asyncio
        import json
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from service_route_sync.probes.work_probes import probe_work_endpoint

        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        stdout.feed_data(
            json.dumps(
                {
                    "http_code": "200",
                    "time_namelookup": 0.01,
                    "time_connect": 0.02,
                    "time_appconnect": 0.03,
                    "time_starttransfer": 0.04,
                    "time_total": 0.05,
                }
            ).encode()
        )
        stdout.feed_eof()
        stderr.feed_eof()
        process = SimpleNamespace(
            stdout=stdout, stderr=stderr, returncode=0, wait=AsyncMock(return_value=0)
        )
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as execute:
            result = await probe_work_endpoint("m365-login", lambda: NOW)
        self.assertTrue(result.transport_ok)
        args = execute.call_args.args
        self.assertEqual(args[:2], ("/usr/bin/curl", "-q"))
        self.assertEqual(args[args.index("--max-filesize") + 1], "1048576")
        self.assertEqual(set(execute.call_args.kwargs["env"]), {"PATH", "LANG", "LC_ALL"})
