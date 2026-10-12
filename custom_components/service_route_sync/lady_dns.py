"""Fixed house-resolver probes; no caller-selected destination or DNS name."""

import asyncio
from datetime import datetime, timezone
import ipaddress
import secrets
import socket
import struct
import time

QUESTION = b"\x09epicgames\x03com\x00\x00\x01\x00\x01"
LAN_NETWORKS = tuple(
    ipaddress.IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def validate_dns_servers(raw):
    """Allow exactly two distinct, explicit private IPv4 resolvers."""
    if not isinstance(raw, (tuple, list)) or len(raw) != 2:
        raise ValueError("Two DNS resolvers must be configured")
    servers = []
    for value in raw:
        if not isinstance(value, str):
            raise ValueError("DNS resolver must be an IPv4 address")
        try:
            address = ipaddress.IPv4Address(value)
        except ipaddress.AddressValueError:
            raise ValueError("DNS resolver must be an IPv4 address") from None
        if not any(address in network for network in LAN_NETWORKS):
            raise ValueError("DNS resolver must be a private LAN IPv4 address")
        servers.append(str(address))
    if len(set(servers)) != 2:
        raise ValueError("DNS resolvers must differ")
    return tuple(servers)


def query_packet(identifier):
    return struct.pack("!6H", identifier, 0x0100, 1, 0, 0, 0) + QUESTION


def response_ok(packet, identifier):
    """Validate transaction, echoed question and bounded resource records."""
    if len(packet) < 12 + len(QUESTION):
        raise ValueError("Invalid DNS response length")
    ident, flags, questions, answers, _, _ = struct.unpack("!6H", packet[:12])
    if (
        ident != identifier
        or not flags & 0x8000
        or flags & 0x7800
        or questions != 1
        or packet[12 : 12 + len(QUESTION)] != QUESTION
    ):
        raise ValueError("Invalid DNS response identity")
    if flags & 0x0200:
        raise ValueError("Truncated DNS response")
    if flags & 15:
        return False
    position = 12 + len(QUESTION)
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


async def probe_dns(server, allowed_servers):
    if server not in allowed_servers:
        raise ValueError("DNS destination is not configured")
    identifier = secrets.randbelow(65536)
    query = query_packet(identifier)
    started = time.monotonic()
    outcome = {"ok": None, "ms": None, "error": None}
    try:
        async with asyncio.timeout(4):
            loop = asyncio.get_running_loop()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
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
            outcome["ok"] = response_ok(packet, identifier)
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


class LadyDNSReader:
    def __init__(self, servers):
        self.servers = validate_dns_servers(servers)

    async def __call__(self):
        from .lady_coordinator import TimedObservation

        values = await asyncio.gather(*(probe_dns(server, self.servers) for server in self.servers))
        return TimedObservation(
            dict(zip(self.servers, values, strict=True)), datetime.now(timezone.utc).timestamp()
        )
