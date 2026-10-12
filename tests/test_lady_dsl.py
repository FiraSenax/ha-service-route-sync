from datetime import datetime, timezone
import copy
import unittest
import bootstrap  # noqa: F401
from service_route_sync.lady_dsl import parse_dsl, DSLDataError


class DSLProtocolTests(unittest.TestCase):
    def raw(self):
        row = {"loss": 0, "avg": 10, "max": 11, "rc": 0, "error": None}
        return {
            "schema": 1,
            "observed_at_ms": 1000000,
            "probes": {"wan_cf": dict(row), "wan_google": dict(row)},
        }

    def parse(self, raw):
        return parse_dsl(raw, datetime.fromtimestamp(1000, timezone.utc))

    def test_valid_result_is_immutable_and_not_aliased(self):
        raw = self.raw()
        result = self.parse(raw)
        raw["probes"]["wan_cf"]["avg"] = 999
        self.assertEqual(result.probes["wan_cf"]["avg"], 10)
        with self.assertRaises(TypeError):
            result.probes["wan_cf"]["avg"] = 12

    def test_missing_route_and_complete_loss_remain_distinct(self):
        raw = self.raw()
        raw["probes"]["wan_cf"] = {
            "loss": None,
            "avg": None,
            "max": None,
            "rc": 2,
            "error": "dsl_route_unavailable",
        }
        raw["probes"]["wan_google"] = {
            "loss": 100,
            "avg": None,
            "max": None,
            "rc": 1,
            "error": None,
        }
        result = self.parse(raw)
        self.assertIsNone(result.probes["wan_cf"]["loss"])
        self.assertEqual(result.probes["wan_google"]["loss"], 100)

    def test_corrupt_and_inconsistent_measurements_are_rejected(self):
        for key, value in [
            ("loss", True),
            ("avg", float("nan")),
            ("max", 9),
            ("rc", 1),
            ("error", "dsl_route_changed"),
        ]:
            raw = copy.deepcopy(self.raw())
            raw["probes"]["wan_cf"][key] = value
            with self.assertRaises(DSLDataError):
                self.parse(raw)

    def test_closed_targets_and_freshness(self):
        for stamp in (979000, 1000001, True):
            with self.assertRaises(DSLDataError):
                self.parse(dict(self.raw(), observed_at_ms=stamp))
        raw = self.raw()
        raw["probes"]["foreign"] = {}
        with self.assertRaises(DSLDataError):
            self.parse(raw)
