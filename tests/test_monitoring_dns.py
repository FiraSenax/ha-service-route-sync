import struct
import unittest
import bootstrap  # noqa: F401
from service_route_sync.monitoring.monitoring_dns import (
    QUESTION,
    dns_question,
    response_ok,
    probe_dns,
    validate_dns_servers,
    MonitoringDNSReader,
)


class DNSTests(unittest.IsolatedAsyncioTestCase):
    def answer(self, identifier=12, flags=0x8180, tail=None):
        if tail is None:
            tail = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + b"\x01\x02\x03\x04"
        return struct.pack("!6H", identifier, flags, 1, 1, 0, 0) + QUESTION + tail

    def test_custom_query_and_ipv6_public_resolvers(self):
        self.assertEqual(
            validate_dns_servers(["2606:4700:4700::1111", "8.8.8.8"]),
            ("2606:4700:4700::1111", "8.8.8.8"),
        )
        question = dns_question("status.example.net")
        packet = (
            struct.pack("!6H", 12, 0x8180, 1, 1, 0, 0)
            + question
            + b"\xc0\x0c"
            + struct.pack("!HHIH", 1, 1, 60, 4)
            + b"\x01\x02\x03\x04"
        )
        self.assertTrue(response_ok(packet, 12, question))
        with self.assertRaises(ValueError):
            response_ok(packet, 12)
        for invalid in [
            "",
            "localhost",
            "a..example",
            "https://example.com",
            "-bad.example",
            "x" * 64 + ".example",
        ]:
            with self.assertRaises(ValueError):
                dns_question(invalid)

    def test_valid_answer_and_explicit_dns_failure(self):
        self.assertTrue(response_ok(self.answer(), 12))
        self.assertFalse(response_ok(self.answer(flags=0x8183), 12))

    def test_wrong_id_truncation_and_invalid_records_are_unknown(self):
        for packet in (
            self.answer(identifier=13),
            self.answer(flags=0x8380),
            self.answer()[:-1],
            self.answer(tail=b"\xc0\xff"),
        ):
            with self.assertRaises(ValueError):
                response_ok(packet, 12)

    async def test_caller_cannot_select_other_resolver(self):
        with self.assertRaises(ValueError):
            await probe_dns("8.8.8.8", ("192.168.1.10", "192.168.1.11"))

    def test_resolvers_are_explicit_and_lan_only(self):
        self.assertEqual(
            MonitoringDNSReader(["192.168.1.10", "10.0.0.53"]).servers,
            ("192.168.1.10", "10.0.0.53"),
        )
        for invalid in (
            None,
            ["192.168.1.10"],
            ["192.168.1.10"] * 2,
            ["127.0.0.1", "192.168.1.10"],
            ["192.0.2.1", "192.168.1.10"],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_dns_servers(invalid)
