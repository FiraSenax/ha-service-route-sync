"""Bounded probes for explicitly configured DNS resolvers and query names."""

import asyncio
from datetime import datetime, timezone
import ipaddress
import secrets
import re
import socket
import struct
import time


def dns_question(domain):
    if not isinstance(domain, str) or len(domain) > 253:
        raise ValueError("Invalid DNS probe name")
    labels = domain.rstrip(".").lower().split(".")
    if len(labels) < 2 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
    ):
        raise ValueError("Invalid DNS probe name")
    return (
        b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels)
        + b"\x00\x00\x01\x00\x01"
    )


QUESTION = dns_question("example.com")


def validate_dns_servers(raw):
    """Two explicit unicast resolvers; no hostnames, local sockets or scope IDs."""
    if not isinstance(raw, (tuple, list)) or len(raw) != 2:
        raise ValueError("Two DNS resolvers must be configured")
    servers = []
    for value in raw:
        if not isinstance(value, str) or "%" in value:
            raise ValueError("DNS resolver must be an IP address")
        address = ipaddress.ip_address(value)
        private_lan = any(
            address in network
            for network in (
                ipaddress.ip_network("10.0.0.0/8"),
                ipaddress.ip_network("172.16.0.0/12"),
                ipaddress.ip_network("192.168.0.0/16"),
                ipaddress.ip_network("fc00::/7"),
            )
        )
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or not (private_lan or address.is_global)
        ):
            raise ValueError("Unsupported DNS resolver address")
        servers.append(str(address))
    if len(set(servers)) != 2:
        raise ValueError("DNS resolvers must differ")
    return tuple(servers)


def query_packet(identifier, question=QUESTION):
    return struct.pack("!6H", identifier, 0x0100, 1, 0, 0, 0) + question


def response_ok(packet, identifier, question=QUESTION):
    """Validate transaction, echoed question and bounded resource records."""
    if len(packet) < 12 + len(question):
        raise ValueError("Invalid DNS response length")
    ident, flags, questions, answers, _, _ = struct.unpack("!6H", packet[:12])
    if (
        ident != identifier
        or not flags & 0x8000
        or flags & 0x7800
        or questions != 1
        or packet[12 : 12 + len(question)] != question
    ):
        raise ValueError("Invalid DNS response identity")
    if flags & 0x0200:
        raise ValueError("Truncated DNS response")
    if flags & 15:
        return False
    position = 12 + len(question)
    found = False
    for _ in range(answers):
        for _ in range(128):
            if position >= len(packet):
                raise ValueError("Incomplete DNS answer")
            length = packet[position]
            if length & 0xC0 == 0xC0:
                if (
                    position + 1 >= len(packet)
                    or ((length & 63) << 8 | packet[position + 1]) >= position
                ):
                    raise ValueError("Invalid DNS name pointer")
                position += 2
                break
            if length > 63:
                raise ValueError("Invalid DNS label")
            position += 1
            if length == 0:
                break
            position += length
        else:
            raise ValueError("DNS name exceeds bound")
        if position + 10 > len(packet):
            raise ValueError("Incomplete DNS resource header")
        kind, klass, _, size = struct.unpack("!HHIH", packet[position : position + 10])
        position += 10
        if position + size > len(packet):
            raise ValueError("Incomplete DNS resource data")
        if kind == 1 and klass == 1 and size == 4:
            found = True
        position += size
    return found


async def probe_dns(server, allowed_servers, question=QUESTION):
    if server not in allowed_servers:
        raise ValueError("DNS destination is not configured")
    identifier = secrets.randbelow(65536)
    query = query_packet(identifier, question)
    started = time.monotonic()
    outcome = {"ok": None, "ms": None, "error": None}
    try:
        async with asyncio.timeout(4):
            loop = asyncio.get_running_loop()
            with socket.socket(
                socket.AF_INET6 if ":" in server else socket.AF_INET, socket.SOCK_DGRAM
            ) as channel:
                channel.setblocking(False)
                await loop.sock_connect(channel, (server, 53))
                await loop.sock_sendall(channel, query)
                packet = await loop.sock_recv(channel, 4096)
            if len(packet) >= 4 and struct.unpack("!H", packet[2:4])[0] & 0x0200:
                reader, writer = await asyncio.open_connection(server, 53)
                try:
                    writer.write(struct.pack("!H", len(query)) + query)
                    await writer.drain()
                    size = struct.unpack("!H", await reader.readexactly(2))[0]
                    if not 12 <= size <= 4096:
                        raise ValueError("DNS TCP response exceeds bound")
                    packet = await reader.readexactly(size)
                finally:
                    writer.close()
            outcome["ok"] = response_ok(packet, identifier, question)
            outcome["ms"] = round((time.monotonic() - started) * 1000, 2)
            if not outcome["ok"]:
                outcome["error"] = "dns_failure"
    except TimeoutError:
        # A timed-out actual DNS request is evidence of resolver failure.
        outcome.update(ok=False, error="timeout")
    except (OSError, asyncio.IncompleteReadError):
        outcome["error"] = "transport_failure"
    except ValueError:
        outcome["error"] = "invalid_response"
    return outcome


class MonitoringDNSReader:
    def __init__(self, servers, domain="example.com"):
        self.servers = validate_dns_servers(servers)
        self.question = dns_question(domain)

    async def __call__(self):
        from .monitoring_coordinator import TimedObservation

        values = await asyncio.gather(
            *(probe_dns(server, self.servers, self.question) for server in self.servers)
        )
        return TimedObservation(
            dict(zip(self.servers, values, strict=True)), datetime.now(timezone.utc).timestamp()
        )
