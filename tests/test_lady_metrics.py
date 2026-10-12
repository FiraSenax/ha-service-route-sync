import unittest
import bootstrap  # noqa: F401
from service_route_sync.lady_metrics import render_lady


def snapshot(now=1000):
    return {'observed_at': now, 'row': {'port_identity': 'Synthetic Port 3', 'port': {'up': True, 'speed': 2500, 'rx_errors': None},
                                      'probes': {'lan': {'loss': 0, 'avg': 1, 'max': 2}, 'wan_cf': {'loss': 100, 'avg': None, 'max': None}},
                                      'dns': {'172.20.1.253': {'ok': True, 'ms': 1}}, 'windows': {'agent': {'last_keepalive': now}}},
            'engine': {'expected_power_off': False, 'incidents': {'synthetic': {'active': True}}},
            'pending': 2, 'sources': {key: {'ok': True, 'observed_at': now} for key in ('api', 'probe', 'windows', 'dns253', 'dns254')},
            'history': {'counts': {'synthetic': 1}, 'recent': [{'key': 'synthetic', 'message': 'quote " and\nnewline', 'time': now, 'recovery': False}]}}


class LadyMetricsTests(unittest.TestCase):
    def test_metric_names_labels_and_missing_values(self):
        text = render_lady(snapshot(), 1000)
        self.assertIn('lady_port_speed{port="Synthetic Port 3"} 2500.0', text)
        self.assertNotIn('lady_port_rx_errors', text)
        self.assertIn('lady_probe_loss_percent{target="wan_cf"} 100.0', text)
        self.assertNotIn('lady_probe_rtt_ms{stat="avg",target="wan_cf"}', text)
        self.assertIn('lady_recorded_events{category="synthetic"} 1.0', text)
        self.assertIn('quote \\" and\\nnewline', text)

    def test_stale_and_future_snapshots_never_export_healthy_network(self):
        for now in (1046, 999):
            text = render_lady(snapshot(), now)
            self.assertIn('lady_monitor_fresh 0.0', text)
            self.assertIn('lady_pc_state -1.0', text)
            self.assertNotIn('lady_port_speed', text)

    def test_duplicate_event_series_is_emitted_once_but_counts_remain(self):
        data = snapshot()
        data['history']['recent'] *= 2
        text = render_lady(data, 1000)
        self.assertEqual(sum(line.startswith('lady_event_timestamp_seconds') for line in text.splitlines()), 1)
        self.assertIn('lady_recorded_events{category="synthetic"} 1.0', text)

    def test_windows_line_endings_are_safe_labels_without_changing_history(self):
        data = snapshot()
        message = 'line1\r\nline2\x00'
        data['history']['recent'][0]['message'] = message
        text = render_lady(data, 1000)
        self.assertIn('line1\\nline2\ufffd', text)
        self.assertEqual(data['history']['recent'][0]['message'], message)
