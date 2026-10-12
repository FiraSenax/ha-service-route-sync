import struct
import unittest
import bootstrap  # noqa: F401
from service_route_sync.lady_dns import (
    QUESTION,
    response_ok,
    probe_dns,
    validate_dns_servers,
    LadyDNSReader,
)


class DNSTests(unittest.IsolatedAsyncioTestCase):
    def answer(self, identifier=12, flags=0x8180, tail=None):
        if tail is None:
            tail = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + b"\x01\x02\x03\x04"
        return struct.pack("!6H", identifier, flags, 1, 1, 0, 0) + QUESTION + tail

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
            LadyDNSReader(["192.168.1.10", "10.0.0.53"]).servers,
            ("192.168.1.10", "10.0.0.53"),
        )
        for invalid in (
            None,
            ["192.168.1.10"],
            ["192.168.1.10"] * 2,
            ["8.8.8.8", "192.168.1.10"],
            ["127.0.0.1", "192.168.1.10"],
            ["192.0.2.1", "192.168.1.10"],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_dns_servers(invalid)
