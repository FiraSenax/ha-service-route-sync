import copy
from datetime import datetime, timezone
import unittest
import bootstrap  # noqa: F401
from service_route_sync.client_probe_protocol import parse_client_probes
from service_route_sync.network import NetworkDataError

NOW = datetime(2026, 10, 11, 20, tzinfo=timezone.utc)
STAMP = int(NOW.timestamp() * 1000)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.raw = {
            "schema": 1,
            "observed_at_ms": STAMP,
            "clients": {
                "pc": {
                    "identity_verified": True,
                    "probe_at_ms": STAMP,
                    "probe": {"loss": 0, "average_ms": 0, "maximum_ms": 0},
                    "routing_at_ms": STAMP,
                    "connections": dict.fromkeys(["dsl", "starlink", "vpn1", "vpn2", "other"], 0),
                }
            },
        }

    def parse(self):
        return parse_client_probes(self.raw, NOW, ("pc",))

    def test_real_zero_immutable_and_input_independent(self):
        result = self.parse()
        self.assertEqual(result.probes["pc"].loss, 0)
        self.raw["clients"]["pc"]["connections"]["dsl"] = 99
        self.assertEqual(result.routes["pc"].connections["dsl"], 0)
        with self.assertRaises(TypeError):
            result.probes["other"] = result.probes["pc"]

    def test_unverified_requires_all_missing(self):
        self.raw["clients"]["pc"]["identity_verified"] = False
        with self.assertRaises(NetworkDataError):
            self.parse()
        self.raw["clients"]["pc"].update(
            probe=None, probe_at_ms=None, connections=None, routing_at_ms=None
        )
        result = self.parse()
        self.assertFalse(result.identity_verified["pc"])
        self.assertEqual(len(result.probes), 0)

    def test_closed_schema_and_identity(self):
        original = copy.deepcopy(self.raw)
        for mutation in [
            lambda j: j.update(extra="private"),
            lambda j: j["clients"].update(other=j["clients"]["pc"]),
            lambda j: j["clients"]["pc"].update(mac="private"),
            lambda j: j.update(schema=True),
        ]:
            self.raw = copy.deepcopy(original)
            mutation(self.raw)
            with self.assertRaises(NetworkDataError):
                self.parse()

    def test_timestamp_boundaries(self):
        for stamp in [True, STAMP + 1, STAMP - 75001, 10**1000, "2026"]:
            self.raw["observed_at_ms"] = stamp
            with self.assertRaises(NetworkDataError):
                self.parse()

    def test_numeric_and_partial_probe_rejected(self):
        for value in [True, "0", -1, 101, float("nan")]:
            self.raw["clients"]["pc"]["probe"]["loss"] = value
            with self.assertRaises(NetworkDataError):
                self.parse()
        self.raw["clients"]["pc"]["probe"] = {"loss": 0, "average_ms": None, "maximum_ms": None}
        with self.assertRaises(NetworkDataError):
            self.parse()

    def test_counter_boolean_and_extra_route_rejected(self):
        for value in [True, -1, "0", 0.5]:
            self.raw["clients"]["pc"]["connections"]["dsl"] = value
            with self.assertRaises(NetworkDataError):
                self.parse()


if __name__ == "__main__":
    unittest.main()
