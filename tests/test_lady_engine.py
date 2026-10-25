import copy
from datetime import datetime, timezone
import unittest
import bootstrap  # noqa: F401
from service_route_sync.lady_engine import LadyIncidentEngine, counter_delta, pc_monitoring_status

NOW = datetime(2026, 10, 12, 1, tzinfo=timezone.utc).timestamp()


class LadyEngineTests(unittest.TestCase):
    def test_pc_monitoring_status_explains_suppressed_alerts(self):
        row = self.active_row()
        row.update(api_ok=True, probe_ok=True)
        row["probes"]["lan"]["loss"] = 100
        self.assertEqual(pc_monitoring_status(row, {}, NOW), "no_pc_evidence")
        row["port"]["up"] = False
        self.assertEqual(pc_monitoring_status(row, {}, NOW), "port_disconnected")
        self.assertEqual(
            pc_monitoring_status(row, {"expected_power_off": True}, NOW), "powered_off"
        )
        row["port"]["up"] = True
        row["probes"]["lan"]["loss"] = 0
        self.assertEqual(pc_monitoring_status(row, {}, NOW), "active")
        row["probes"]["lan"]["loss"] = None
        row["windows"] = {"agent": {"last_keepalive": NOW - 15}}
        self.assertEqual(pc_monitoring_status(row, {}, NOW), "active")
        row["api_ok"] = False
        self.assertEqual(pc_monitoring_status(row, {}, NOW), "source_unavailable")

    def test_missing_dns_source_breaks_existing_recovery_timer(self):
        engine = LadyIncidentEngine(
            {"incidents": {"DNS test-resolver": {"active": True, "since": None, "good": NOW - 20}}}
        )
        row = self.active_row()
        row.pop("dns")
        engine.sample(row, NOW)
        self.assertIsNone(engine.s["incidents"]["DNS test-resolver"]["good"])
        row["dns"] = {"test-resolver": {"ok": True}}
        engine.sample(row, NOW + 20)
        self.assertTrue(engine.s["incidents"]["DNS test-resolver"]["active"])
        engine.sample(row, NOW + 51)
        self.assertFalse(engine.s["incidents"]["DNS test-resolver"]["active"])

    def test_unknown_link_is_not_an_offline_transition(self):
        engine = LadyIncidentEngine()
        row = self.row()
        row["port"]["up"] = None
        engine.sample(row, NOW)
        self.assertNotIn("down_since", engine.s)
        row["port"]["up"] = True
        events = engine.sample(row, NOW + 5)
        self.assertFalse(any(e["key"] == "LAN-Unterbrechung" for e in events))

    def test_link_flap_counter_types_and_gap_do_not_recover_active_incident(self):
        engine = LadyIncidentEngine()
        row = {
            "api_ok": True,
            "port_identity": "test",
            "port": {"link_down_count": True, "up": True},
            "switch_uptime": "10",
        }
        engine.link_flaps(row, NOW, False, False)
        self.assertNotIn("link_flap_window", engine.s)
        row["port"]["link_down_count"] = 10
        row["switch_uptime"] = 100
        engine.s["up_since"] = NOW - 1000
        engine.link_flaps(row, NOW, False, False)
        engine.s["incidents"]["LAN-Linkabbrüche gehäuft"]["active"] = True
        engine.link_flaps(row, NOW + 121, False, False)
        self.assertTrue(engine.s["incidents"]["LAN-Linkabbrüche gehäuft"]["active"])

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

    def active_row(self):
        row = self.row()
        row["port"]["speed"] = 2500
        row["probes"]["lan"] = {"loss": 0, "avg": 1}
        return row

    def test_expected_shutdown_suppresses_all_lady_notifications(self):
        engine = LadyIncidentEngine({"power_off": NOW - 10, "power_on": NOW - 1000})
        engine.sample(self.row(), NOW)
        events = engine.sample(self.row(), NOW + 31)
        self.assertTrue(engine.s["expected_power_off"])
        self.assertEqual(events, [])
        self.assertFalse(any(value["active"] for value in engine.s["incidents"].values()))

    def test_persisted_incident_and_notification_dedup_survive_restore(self):
        engine = LadyIncidentEngine()
        engine.sample(self.active_row(), NOW)
        engine.sample(self.active_row(), NOW + 31)
        checkpoint = copy.deepcopy(engine.s)
        restored = LadyIncidentEngine(checkpoint)
        self.assertFalse(restored.sample(self.active_row(), NOW + 40))
        self.assertTrue(restored.s["incidents"]["Primär-WAN-Paketverlust"]["active"])
        restored.s["power_on"] = NOW + 41
        self.assertNotIn("power_on", checkpoint)

    def test_wan_recovery_requires_existing_stability_window(self):
        engine = LadyIncidentEngine()
        engine.sample(self.active_row(), NOW)
        engine.sample(self.active_row(), NOW + 31)
        row = self.active_row()
        row["probes"]["wan_cf"]["loss"] = 0
        row["probes"]["wan_google"]["loss"] = 0
        engine.sample(row, NOW + 40)
        self.assertTrue(engine.s["incidents"]["Primär-WAN-Paketverlust"]["active"])
        events = engine.sample(row, NOW + 71)
        self.assertTrue(
            any(event["key"] == "Primär-WAN-Paketverlust" and event["recovery"] for event in events)
        )

    def test_disconnected_pc_clears_stale_incidents_without_recovery(self):
        engine = LadyIncidentEngine()
        engine.sample(self.active_row(), NOW)
        engine.sample(self.active_row(), NOW + 31)
        offline = self.row()
        offline["port"]["up"] = False
        self.assertEqual(engine.sample(offline, NOW + 40), [])
        self.assertEqual(engine.sample(offline, NOW + 200), [])
        self.assertFalse(any(value["active"] for value in engine.s["incidents"].values()))
        self.assertEqual(engine.sample(self.active_row(), NOW + 210), [])

    def test_wake_on_lan_link_requires_positive_pc_evidence(self):
        engine = LadyIncidentEngine()
        row = self.row()
        for seconds in (0, 31, 150, 300):
            self.assertEqual(engine.sample(row, NOW + seconds), [])
        row["windows"] = {"agent": {"last_keepalive": NOW + 300}}
        engine.sample(row, NOW + 305)
        events = engine.sample(row, NOW + 336)
        self.assertTrue(any(event["key"] == "Primär-WAN-Paketverlust" for event in events))

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
        self.assertTrue(engine.s["incidents"]["Primär-WAN-Latenz"]["active"])
        for value in row["probes"].values():
            value["avg"] = None
        engine.wan_dns(row, NOW + 20)
        engine.wan_dns(row, NOW + 60)
        self.assertTrue(engine.s["incidents"]["Primär-WAN-Latenz"]["active"])

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
