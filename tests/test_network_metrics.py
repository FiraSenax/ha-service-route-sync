import unittest

import bootstrap  # noqa: F401
from service_route_sync.network.network_metrics import MetricDataError, MetricWriter


class MetricTests(unittest.TestCase):
    def test_missing_measurement_omitted_but_real_zero_retained(self):
        writer = MetricWriter()
        writer.add("gaming_probe_avg", None, pc="test")
        writer.add("gaming_probe_loss", 0, pc="test")
        self.assertNotIn("gaming_probe_avg", writer.render())
        self.assertIn('gaming_probe_loss{pc="test"} 0.0', writer.render())

    def test_label_cannot_inject_another_sample(self):
        writer = MetricWriter()
        writer.add("gaming_port_info", 1, pc="test", port="port", name='"\\\nforged_metric 999')
        self.assertEqual(len(writer.render().splitlines()), 1)
        self.assertIn("\\nforged_metric", writer.render())

    def test_unexpected_names_and_labels_rejected_without_private_data(self):
        for name, labels in [
            ("private-secret", {}),
            ("gaming_probe_loss", {"pc": "test", "token": "private-secret"}),
        ]:
            with self.assertRaises(MetricDataError) as captured:
                MetricWriter().add(name, 0, **labels)
            self.assertNotIn("private-secret", str(captured.exception))

    def test_nonfinite_and_implicit_measurement_coercions_rejected(self):
        for value in [True, "5", float("nan"), float("inf"), -1, 10**1000]:
            with self.assertRaises(MetricDataError):
                MetricWriter().add("gaming_probe_loss", value, pc="test")

    def test_status_sentinels_supported_but_arbitrary_states_rejected(self):
        for value in [-1, 0, 1, 2]:
            MetricWriter().add("gaming_pc_state", value, pc="test")
        with self.assertRaises(MetricDataError):
            MetricWriter().add("gaming_pc_state", 3, pc="test")
        with self.assertRaises(MetricDataError):
            MetricWriter().add("gaming_monitor_fresh", 2, pc="test")

    def test_duplicate_time_series_is_error(self):
        writer = MetricWriter()
        writer.add("gaming_probe_loss", 0, pc="test")
        with self.assertRaises(MetricDataError):
            writer.add("gaming_probe_loss", 1, pc="test")

    def test_label_payload_bounds(self):
        for value in [None, {"secret": "test"}, "a" * 2049, "bad\x00text", "\ud800"]:
            with self.assertRaises(MetricDataError):
                MetricWriter().add("gaming_probe_loss", 0, pc=value)


if __name__ == "__main__":
    unittest.main()
