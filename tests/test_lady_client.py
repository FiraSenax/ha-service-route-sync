from datetime import datetime, timezone
import unittest
import bootstrap  # noqa: F401
from service_route_sync.client_observation import ClientObservation, PortObservation
from service_route_sync.lady_client import incident_port


class LadyClientTests(unittest.TestCase):
    def client(self, ambiguous=False, port=True):
        p = PortObservation(
            "02:00:00:00:00:01:3",
            "Synthetic Switch Port 3",
            True,
            2500,
            None,
            2,
            None,
            3,
            4,
            5,
            None,
            100,
        )
        return ClientObservation(
            datetime.now(timezone.utc),
            True,
            "wired",
            "172.20.1.10",
            p if port else None,
            ambiguous,
            None,
        )

    def test_missing_counters_stay_unknown_and_identity_is_preserved(self):
        result = incident_port(self.client())
        self.assertEqual(result["port_identity"], "Synthetic Switch Port 3")
        self.assertEqual(result["tracked_port"], {"switch": "02:00:00:00:00:01", "port": 3})
        self.assertIsNone(result["port"]["rx_errors"])
        self.assertIsNone(result["port"]["link_down_count"])
        self.assertEqual(result["port"]["speed"], 2500)

    def test_no_arbitrary_port_from_ambiguous_or_absent_source(self):
        self.assertEqual(incident_port(self.client(ambiguous=True)), {})
        self.assertEqual(incident_port(self.client(port=False)), {})
