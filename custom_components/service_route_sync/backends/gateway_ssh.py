"""Bounded native OpenSSH transport for one read-only forced command.

No shell, SSH agent, fallback password, caller-supplied command or host-key
acceptance is allowed. The helper's private raw responses never enter logs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import stat
from typing import Any

from ..network.network import NetworkDataError

_COMMAND = "service-route-sync observe v1"
_MAX_RESPONSE = 65_536


class GatewayTransportError(Exception):
    """A sanitized transport failure; never include SSH output."""


@dataclass(frozen=True, slots=True)
class GatewaySSHConfig:
    host: str
    credential_directory: Path
    port: int = 22

    def __post_init__(self) -> None:
        if not isinstance(self.host, str):
            raise ValueError("Gateway must have a numeric IP address")
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError:
            raise ValueError("Gateway must have a numeric IP address") from None
        if (
            address.is_loopback
            or address.is_unspecified
            or address.is_multicast
            or "%" in self.host
        ):
            raise ValueError("Unsupported gateway address")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Invalid gateway port")

    @property
    def private_key(self) -> Path:
        return self.credential_directory / "observer_ed25519"

    @property
    def known_hosts(self) -> Path:
        return self.credential_directory / "known_hosts"

    def validate_files(self) -> None:
        """Call in HA's executor; only an owned, private credential directory."""
        directory = self.credential_directory.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.geteuid()
            or directory.st_mode & 0o077
        ):
            raise GatewayTransportError("Unsafe gateway credential directory")
        for path, forbidden_mode in ((self.private_key, 0o077), (self.known_hosts, 0o022)):
            try:
                info = path.lstat()
            except OSError:
                raise GatewayTransportError("Missing gateway trust material") from None
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & forbidden_mode
            ):
                raise GatewayTransportError("Unsafe gateway trust material")

    def arguments(self) -> tuple[str, ...]:
        return (
            "/usr/bin/ssh",
            "-T",
            "-F",
            "/dev/null",
            "-i",
            str(self.private_key),
            "-p",
            str(self.port),
            "-o",
            f"UserKnownHostsFile={self.known_hosts}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UpdateHostKeys=no",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "IdentityAgent=none",
            "-o",
            "ProxyCommand=none",
            "-o",
            "ProxyJump=none",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "PermitLocalCommand=no",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "GSSAPIAuthentication=no",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "NumberOfPasswordPrompts=0",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "LogLevel=ERROR",
            f"root@{self.host}",
            _COMMAND,
        )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise NetworkDataError("Duplicate gateway JSON field")
        result[key] = value
    return result


async def _read_bounded(stream: asyncio.StreamReader, maximum: int) -> bytes:
    result = bytearray()
    while chunk := await stream.read(min(4096, maximum + 1 - len(result))):
        result.extend(chunk)
        if len(result) > maximum:
            raise GatewayTransportError("Gateway response exceeded the size limit")
    return bytes(result)


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 2)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()


async def read_gateway(
    config: GatewaySSHConfig, *, timeout: float = 12, input_data: bytes | None = None
) -> Any:
    """Compatibility entry point for the existing fixed observation profiles."""
    return await exchange_pinned_command(config, timeout=timeout, input_data=input_data)


async def exchange_pinned_command(
    config: GatewaySSHConfig, *, timeout: float = 12, input_data: bytes | None = None
) -> Any:
    """Exchange a bounded JSON response using a code-defined pinned command.

    Credential-file validation belongs in HA's executor before this call.
    Each profile owns its fixed command and any required authorization. This
    transport never accepts a command from service input or exposes stderr.
    Cancellation stops the local child; a remote write may already be committed.
    """
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
    if input_data is not None and (not isinstance(input_data, bytes) or len(input_data) > 16384):
        raise GatewayTransportError("Invalid gateway input frame")
    try:
        process = await asyncio.create_subprocess_exec(
            *config.arguments(),
            stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
    except OSError:
        raise GatewayTransportError("Native SSH client is unavailable") from None
    try:
        async with asyncio.timeout(timeout):
            async with asyncio.TaskGroup() as tasks:
                output = tasks.create_task(_read_bounded(process.stdout, _MAX_RESPONSE))
                tasks.create_task(_read_bounded(process.stderr, 4096))
                tasks.create_task(process.wait())
                if input_data is not None:

                    async def send():
                        process.stdin.write(input_data)
                        await process.stdin.drain()
                        process.stdin.close()
                        await process.stdin.wait_closed()

                    tasks.create_task(send())
        if process.returncode != 0:
            raise GatewayTransportError("Gateway SSH request failed")
        try:
            return json.loads(output.result(), object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError):
            raise GatewayTransportError("Invalid gateway observation response") from None
    except TimeoutError:
        raise GatewayTransportError("Gateway observation timed out") from None
    except ExceptionGroup:
        raise GatewayTransportError("Gateway observation stream failed") from None
    finally:
        await _stop(process)
