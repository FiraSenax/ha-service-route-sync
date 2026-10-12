from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

import bootstrap  # noqa: F401
from service_route_sync.client_metrics import ProbeObservation, RoutingObservation, render_clients
from service_route_sync.client_observation import observe_client
from service_route_sync.network_metrics import MetricDataError

NOW = datetime(2026, 10, 11, 20, tzinfo=timezone.utc)
MAC = "02:00:00:00:00:01"


class ClientMetricTests(unittest.TestCase):
    def snapshot(self, online=True):
        rows = [{"mac": MAC, "is_wired": True, "last_seen": NOW.timestamp()}] if online else []
        return SimpleNamespace(
            observed_at=NOW, clients={"pc": observe_client([MAC], rows, [], NOW)}
        )

    def test_missing_probes_are_not_invented_success(self):
        text = render_clients(("pc",), self.snapshot(), True, {}, {}, NOW)
        self.assertIn('gaming_pc_state{pc="pc"} -1.0', text)
        self.assertIn('gaming_source_ok{pc="pc",source="probe"} 0.0', text)
        self.assertNotIn("gaming_sample_timestamp_seconds", text)
        self.assertNotIn("gaming_probe_avg", text)

    def test_real_probe_zero_and_routing_counts_preserved(self):
        probe = ProbeObservation(NOW, 0, 0, 0)
        route = RoutingObservation(NOW, True, {"dsl": 0, "starlink": 3})
        text = render_clients(("pc",), self.snapshot(), True, {"pc": probe}, {"pc": route}, NOW)
        self.assertIn('gaming_pc_state{pc="pc"} 1.0', text)
        self.assertIn('gaming_probe_avg{pc="pc"} 0.0', text)
        self.assertIn('gaming_connections{pc="pc",route="starlink"} 3.0', text)

    def test_stale_failed_and_future_api_omits_cached_metrics(self):
        for delta, ok in [(-75, True), (1, True), (0, False)]:
            snapshot = self.snapshot()
            snapshot.observed_at = NOW + timedelta(seconds=delta)
            text = render_clients(("pc",), snapshot, ok, {}, {}, NOW)
            self.assertNotIn("gaming_client_online", text)
            self.assertIn('gaming_pc_state{pc="pc"} -1.0', text)

    def test_offline_retains_offline_semantics(self):
        text = render_clients(("pc",), self.snapshot(False), True, {}, {}, NOW)
        self.assertIn('gaming_pc_state{pc="pc"} 0.0', text)

    def test_probe_boundaries_and_routing_mutability(self):
        for value in [-1, 101, True, "0", float("nan")]:
            with self.assertRaises(MetricDataError):
                ProbeObservation(NOW, value, 0, 0)
        counts = {"dsl": 1}
        route = RoutingObservation(NOW, True, counts)
        counts["dsl"] = 99
        self.assertEqual(route.connections["dsl"], 1)
        for stamp in [NOW - timedelta(seconds=20), NOW + timedelta(seconds=1)]:
            text = render_clients(
                ("pc",), self.snapshot(), True, {"pc": ProbeObservation(stamp, 0, 0, 0)}, {}, NOW
            )
            self.assertNotIn("gaming_probe_loss", text)

    def test_completed_cycle_preserves_offline_sample_timestamp_without_ping(self):
        text = render_clients(("pc",), self.snapshot(False), True, {}, {}, NOW, probe_cycle_at=NOW)
        self.assertIn('gaming_sample_timestamp_seconds{pc="pc"}', text)
        self.assertIn('gaming_monitor_fresh{pc="pc"} 1.0', text)
        self.assertIn('gaming_source_ok{pc="pc",source="probe"} 1.0', text)
        self.assertIn('gaming_pc_state{pc="pc"} 0.0', text)
        self.assertNotIn("gaming_probe_loss", text)


if __name__ == "__main__":
    unittest.main()
