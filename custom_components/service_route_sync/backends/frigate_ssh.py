"""Dedicated pinned Frigate scope with two immutable protocol commands."""

import base64
import binascii
import ipaddress
import math
import re
import struct
from datetime import datetime

from .gateway_ssh import GatewaySSHConfig, GatewayTransportError, exchange_pinned_command


class FrigateStatusSSHConfig(GatewaySSHConfig):
    def __post_init__(self):
        super().__post_init__()
        address = ipaddress.ip_address(self.host)
        if address.version != 4 or not address.is_private or self.port != 22:
            raise ValueError("Unsupported Frigate peer")

    @property
    def private_key(self):
        return self.credential_directory / "id_ed25519"

    def validate_files(self):
        super().validate_files()
        for path in (self.private_key, self.known_hosts):
            info = path.lstat()
            if info.st_mode & 0o077 or info.st_nlink != 1:
                raise GatewayTransportError("Unsafe Frigate trust material")
        try:
            with self.known_hosts.open("rb") as source:
                value = source.read(4097)
            if len(value) > 4096:
                raise ValueError
            fields = value.decode("ascii").split()
            if len(fields) != 3 or fields[:2] != [self.host, "ssh-ed25519"]:
                raise ValueError
            raw = base64.b64decode(fields[2], validate=True)
            if (
                raw[:4] != struct.pack(">I", 11)
                or raw[4:15] != b"ssh-ed25519"
                or raw[15:19] != struct.pack(">I", 32)
                or len(raw) != 51
            ):
                raise ValueError
        except (OSError, UnicodeError, ValueError, binascii.Error):
            raise GatewayTransportError("Invalid Frigate host pin") from None

    def arguments(self):
        return (
            *super().arguments()[:-2],
            "-o",
            "HostKeyAlgorithms=ssh-ed25519",
            f"root@{self.host}",
            "status-v1",
        )


class FrigateRestartSSHConfig(FrigateStatusSSHConfig):
    def arguments(self):
        return (*super().arguments()[:-1], "restart-v1")


def validate_reply(value, action):
    if action not in ("status", "restart"):
        raise GatewayTransportError("Unsupported Frigate response scope")
    expected = {"protocol", "ok", "action", "container_id"}
    expected |= (
        {"running", "started"} if action == "status" else {"attempt_epoch", "restart_requested"}
    )
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or type(value["protocol"]) is not int
        or value["protocol"] != 1
        or value["ok"] is not True
        or value["action"] != action
        or not isinstance(value["container_id"], str)
        or not re.fullmatch("[0-9a-f]{64}", value["container_id"])
    ):
        raise GatewayTransportError("Invalid Frigate actor response")
    if action == "status":
        if (
            type(value["running"]) is not bool
            or not isinstance(value["started"], str)
            or len(value["started"]) > 64
        ):
            raise GatewayTransportError("Invalid Frigate status response")
        try:
            if not value["started"].endswith("Z"):
                raise ValueError
            started = datetime.fromisoformat(value["started"].replace("Z", "+00:00"))
            if value["running"] and started.timestamp() <= 0:
                raise ValueError
        except (ValueError, OverflowError):
            raise GatewayTransportError("Invalid Frigate start timestamp") from None
    else:
        attempt = value["attempt_epoch"]
        if (
            value["restart_requested"] is not True
            or type(attempt) not in (int, float)
            or not math.isfinite(attempt)
            or not 0 < attempt < 2**53
        ):
            raise GatewayTransportError("Invalid Frigate restart response")
    return value


async def read_frigate_status(config):
    if type(config) is not FrigateStatusSSHConfig:
        raise GatewayTransportError("Invalid Frigate observation scope")
    return validate_reply(await exchange_pinned_command(config, timeout=15), "status")


async def request_frigate_restart(config):
    # Only the admin service may reach this operation. Transport success means
    # request accepted, never that all cameras have recovered.
    if type(config) is not FrigateRestartSSHConfig:
        raise GatewayTransportError("Invalid Frigate control scope")
    return validate_reply(await exchange_pinned_command(config, timeout=60), "restart")
