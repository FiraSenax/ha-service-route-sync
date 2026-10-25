import copy
import unittest
from datetime import datetime, timezone

import bootstrap  # noqa: F401
from service_route_sync.clients.client_observation import ClientDataError, observe_client

NOW = datetime(2026, 10, 11, 20, tzinfo=timezone.utc)
MAC = "02:00:00:00:00:01"


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.clients = [
            {"mac": MAC, "last_seen": NOW.timestamp(), "is_wired": True, "ip": "172.20.0.188"}
        ]
        self.devices = [
            {
                "mac": "02:00:00:00:00:02",
                "name": "Switch",
                "last_seen": NOW.timestamp(),
                "uptime": 1234,
                "port_table": [
                    {
                        "port_idx": 1,
                        "up": True,
                        "speed": 2500,
                        "last_connection": {
                            "mac": MAC,
                            "connected": True,
                            "last_seen": NOW.timestamp(),
                        },
                        "rx_errors": 0,
                    }
                ],
            }
        ]

    def observe(self):
        return observe_client([MAC], self.clients, self.devices, NOW)

    def test_current_wired_link_and_exact_units(self):
        row = self.observe()
        self.assertTrue(row.online)
        self.assertTrue(row.port.up)
        self.assertEqual(row.port.speed_mbit_s, 2500)
        self.assertEqual(row.port.rx_errors, 0)
        self.assertIsNone(row.port.tx_errors)

    def test_offline_and_wifi_never_activate_historical_port(self):
        self.clients = []
        self.assertFalse(self.observe().port.up)
        self.setUp()
        self.clients[0]["is_wired"] = False
        self.assertFalse(self.observe().port.up)
        self.assertEqual(self.observe().connection, "wifi")

    def test_stale_or_future_client_is_not_online(self):
        for delta in [-120, 1]:
            self.clients[0]["last_seen"] = NOW.timestamp() + delta
            self.assertFalse(self.observe().online)

    def test_invalid_measurements_are_unknown(self):
        for value in [True, "2500", -1, float("nan"), float("inf")]:
            self.devices[0]["port_table"][0]["speed"] = value
            self.assertIsNone(self.observe().port.speed_mbit_s)

    def test_ambiguous_live_ports_not_selected_by_list_order(self):
        duplicate = copy.deepcopy(self.devices[0])
        duplicate["mac"] = "02:00:00:00:00:03"
        self.devices.append(duplicate)
        self.assertTrue(self.observe().port_ambiguous)
        self.assertIsNone(self.observe().port)
        self.devices.reverse()
        self.assertIsNone(self.observe().port)

    def test_unknown_boolean_does_not_become_active_link(self):
        self.devices[0]["port_table"][0]["up"] = "true"
        self.assertIsNone(self.observe().port.up)

    def test_unknown_client_connection_does_not_become_port_down(self):
        self.clients[0].pop("is_wired")
        self.assertEqual(self.observe().connection, "unknown")
        self.assertIsNone(self.observe().port.up)

    def test_identity_and_payload_boundaries(self):
        for macs in [[], MAC, 123, True, ["ff:ff:ff:ff:ff:ff"], ["invalid"]]:
            with self.assertRaises(ClientDataError):
                observe_client(macs, self.clients, self.devices, NOW)
        with self.assertRaises(ClientDataError):
            observe_client([MAC], {}, self.devices, NOW)
        with self.assertRaises(ClientDataError):
            observe_client([MAC], self.clients, self.devices, NOW.replace(tzinfo=None))

    def test_input_records_not_mutated(self):
        before = copy.deepcopy((self.clients, self.devices))
        self.observe()
        self.assertEqual((self.clients, self.devices), before)

    def test_wifi_signed_signal_and_raw_rates_preserved(self):
        self.clients[0].update(
            is_wired=False,
            signal=-62,
            noise=-95,
            tx_rate=866000,
            rx_rate=433000,
            tx_retries=0,
            tx_packets=42,
        )
        wifi = self.observe().wifi
        self.assertEqual((wifi.signal, wifi.noise), (-62, -95))
        self.assertEqual((wifi.tx_rate, wifi.rx_rate), (866000, 433000))
        self.assertEqual((wifi.tx_retries, wifi.tx_packets), (0, 42))
        self.clients = []
        self.assertIsNone(self.observe().wifi)

    def test_wifi_missing_or_invalid_measurement_is_not_zero(self):
        self.clients[0]["is_wired"] = False
        for value in [True, "-62", float("nan"), float("inf"), 1, -201, 10**1000]:
            self.clients[0]["signal"] = value
            self.assertIsNone(self.observe().wifi.signal)
        self.assertIsNone(self.observe().wifi.tx_packets)
        self.clients[0]["is_wired"] = True
        self.assertIsNone(self.observe().wifi)

    def test_multiple_nics_select_latest_live_client_not_input_order(self):
        other = "02:00:00:00:00:03"
        self.clients[0].update(last_seen=NOW.timestamp() - 5, ip="172.20.1.10")
        self.clients.append(
            {
                "mac": other,
                "last_seen": NOW.timestamp(),
                "ip": "172.20.1.11",
                "is_wired": False,
                "signal": -55,
            }
        )
        first = observe_client([MAC, other], self.clients, self.devices, NOW)
        second = observe_client([MAC, other], list(reversed(self.clients)), self.devices, NOW)
        self.assertEqual(first.ipv4_address, "172.20.1.11")
        self.assertEqual(first, second)
        self.assertFalse(first.port.up)
        self.assertEqual(first.wifi.signal, -55)


if __name__ == "__main__":
    unittest.main()
