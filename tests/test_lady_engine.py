import copy
from datetime import datetime, timezone
import unittest
import bootstrap  # noqa: F401
from service_route_sync.lady_engine import LadyIncidentEngine, counter_delta

NOW = datetime(2026, 10, 12, 1, tzinfo=timezone.utc).timestamp()


class LadyEngineTests(unittest.TestCase):
    def test_missing_dns_source_breaks_existing_recovery_timer(self):
        engine = LadyIncidentEngine({"incidents": {"DNS test-resolver": {"active": True, "since": None, "good": NOW - 20}}})
        engine.sample({}, NOW)
        self.assertIsNone(engine.s["incidents"]["DNS test-resolver"]["good"])
        engine.sample({"dns": {"test-resolver": {"ok": True}}}, NOW + 20)
        self.assertTrue(engine.s["incidents"]["DNS test-resolver"]["active"])
        engine.sample({"dns": {"test-resolver": {"ok": True}}}, NOW + 51)
        self.assertFalse(engine.s["incidents"]["DNS test-resolver"]["active"])

    def test_unknown_link_is_not_an_offline_transition(self):
        engine = LadyIncidentEngine()
        row = self.row()
        row['port']['up'] = None
        engine.sample(row, NOW)
        self.assertNotIn('down_since', engine.s)
        row['port']['up'] = True
        events = engine.sample(row, NOW + 5)
        self.assertFalse(any(e['key'] == 'LAN-Unterbrechung' for e in events))

    def test_link_flap_counter_types_and_gap_do_not_recover_active_incident(self):
        engine = LadyIncidentEngine()
        row = {'api_ok': True, 'port_identity': 'test', 'port': {'link_down_count': True}, 'switch_uptime': '10'}
        engine.link_flaps(row, NOW, False, False)
        self.assertNotIn('link_flap_window', engine.s)
        row['port']['link_down_count'] = 10
        row['switch_uptime'] = 100
        engine.link_flaps(row, NOW, False, False)
        engine.s['incidents']['LAN-Linkabbrüche gehäuft']['active'] = True
        engine.link_flaps(row, NOW + 121, False, False)
        self.assertTrue(engine.s['incidents']['LAN-Linkabbrüche gehäuft']['active'])
    def row(self):
        return {
            "port": {
                "up": True,
                "speed": 10,
                "link_down_count": 0,
                "rx_errors": 0,
                "tx_errors": 0,
                "rx_dropped": 0,
                "tx_dropped": 0,
            },
            "switch_uptime": 1000,
            "probes": {
                "lan": {"loss": 100, "avg": None},
                "wan_cf": {"loss": 100, "avg": None},
                "wan_google": {"loss": 100, "avg": None},
            },
            "dns": {"test-resolver": {"ok": False}},
        }

    def test_expected_shutdown_does_not_disable_wan_or_dns_incidents(self):
        engine = LadyIncidentEngine({"power_off": NOW - 10, "power_on": NOW - 1000})
        engine.sample(self.row(), NOW)
        events = engine.sample(self.row(), NOW + 31)
        self.assertTrue(engine.s["expected_power_off"])
        self.assertTrue(any(event["key"] == "DSL-Paketverlust" for event in events))
        self.assertTrue(any(event["key"] == "DNS test-resolver" for event in events))
        self.assertFalse(any(event["key"].startswith("LAN-") for event in events))

    def test_persisted_incident_and_notification_dedup_survive_restore(self):
        engine = LadyIncidentEngine({"power_off": NOW - 10, "power_on": NOW - 1000})
        engine.sample(self.row(), NOW)
        engine.sample(self.row(), NOW + 31)
        checkpoint = copy.deepcopy(engine.s)
        restored = LadyIncidentEngine(checkpoint)
        self.assertFalse(restored.sample(self.row(), NOW + 40))
        self.assertTrue(restored.s["incidents"]["DSL-Paketverlust"]["active"])
        restored.s["power_on"] = NOW + 41
        self.assertEqual(checkpoint["power_on"], NOW - 1000)

    def test_wan_recovery_requires_existing_stability_window(self):
        engine = LadyIncidentEngine({"power_off": NOW - 10, "power_on": NOW - 1000})
        engine.sample(self.row(), NOW)
        engine.sample(self.row(), NOW + 31)
        row = self.row()
        row["probes"]["wan_cf"]["loss"] = 0
        row["probes"]["wan_google"]["loss"] = 0
        engine.sample(row, NOW + 40)
        self.assertTrue(engine.s["incidents"]["DSL-Paketverlust"]["active"])
        events = engine.sample(row, NOW + 71)
        self.assertTrue(
            any(event["key"] == "DSL-Paketverlust" and event["recovery"] for event in events)
        )

    def test_unknown_interrupts_recovery_without_clearing_active_incident(self):
        engine = LadyIncidentEngine()
        engine.check("test", True, "test", NOW, 10)
        engine.check("test", True, "test", NOW + 11, 10)
        engine.check("test", False, "test", NOW + 20, 10)
        engine.check("test", None, "test", NOW + 40, 10)
        engine.check("test", False, "test", NOW + 60, 10)
        self.assertTrue(engine.s["incidents"]["test"]["active"])
        engine.check("test", False, "test", NOW + 91, 10)
        self.assertFalse(engine.s["incidents"]["test"]["active"])

    def test_unknown_does_not_count_toward_alarm_delay(self):
        engine = LadyIncidentEngine()
        engine.check("test", True, "test", NOW, 10)
        engine.check("test", None, "test", NOW + 9, 10)
        engine.check("test", True, "test", NOW + 30, 10)
        self.assertFalse(engine.s["incidents"]["test"]["active"])

    def test_missing_rtt_cannot_recover_existing_latency_incident(self):
        engine = LadyIncidentEngine()
        row = self.row()
        for value in row["probes"].values():
            value.update(loss=0, avg=100)
        engine.wan_dns(row, NOW)
        engine.wan_dns(row, NOW + 16)
        self.assertTrue(engine.s["incidents"]["DSL-Latenz"]["active"])
        for value in row["probes"].values():
            value["avg"] = None
        engine.wan_dns(row, NOW + 20)
        engine.wan_dns(row, NOW + 60)
        self.assertTrue(engine.s["incidents"]["DSL-Latenz"]["active"])

    def test_counter_delta_rejects_missing_reset_and_invalid_values(self):
        for current, previous in [
            (None, 10),
            (10, None),
            (0, 10),
            (True, 0),
            ("10", 0),
            (float("nan"), 0),
        ]:
            self.assertIsNone(counter_delta(current, previous))
        self.assertEqual(counter_delta(11, 10), 1)
        self.assertEqual(counter_delta(0, 0), 0)

    def test_missing_counter_does_not_create_rebound_error_event(self):
        engine = LadyIncidentEngine()
        row = self.row()
        row["port"]["speed"] = 2500
        row["probes"]["lan"]["loss"] = 0
        row["port"]["rx_errors"] = 10
        engine.sample(row, NOW)
        row["port"]["rx_errors"] = None
        engine.sample(row, NOW + 5)
        row["port"]["rx_errors"] = 10
        events = engine.sample(row, NOW + 10)
        self.assertFalse(any(event["key"] == "Port rx_errors" for event in events))
        row["port"]["rx_errors"] = 11
        events = engine.sample(row, NOW + 15)
        self.assertTrue(any(event["key"] == "Port rx_errors" for event in events))

    def test_malformed_windows_events_do_not_block_valid_power_event(self):
        engine = LadyIncidentEngine()
        stamp = datetime.fromtimestamp(NOW, timezone.utc).isoformat()
        events = [
            None,
            {},
            {"time": stamp, "provider": None, "id": "1074"},
            {"time": stamp, "provider": "User32", "id": True},
            {"time": stamp, "provider": "User32", "id": "1074", "message": {}},
            {"time": stamp, "provider": "User32", "id": 1074},
        ]
        engine.windows(events, NOW)
        self.assertEqual(engine.s["power_off"], NOW)

    def test_future_and_naive_windows_events_cannot_change_power_state(self):
        engine = LadyIncidentEngine()
        for stamp in [
            datetime.fromtimestamp(NOW + 1, timezone.utc).isoformat(),
            datetime.fromtimestamp(NOW, timezone.utc).replace(tzinfo=None).isoformat(),
        ]:
            engine.windows([{"time": stamp, "provider": "User32", "id": "1074"}], NOW)
        self.assertNotIn("power_off", engine.s)

    def test_out_of_order_power_events_never_move_checkpoint_backwards(self):
        engine = LadyIncidentEngine()
        for delta in [0, -30]:
            engine.windows(
                [
                    {
                        "time": datetime.fromtimestamp(NOW + delta, timezone.utc).isoformat(),
                        "provider": "User32",
                        "id": "1074",
                    }
                ],
                NOW,
            )
        self.assertEqual(engine.s["power_off"], NOW)


if __name__ == "__main__":
    unittest.main()
