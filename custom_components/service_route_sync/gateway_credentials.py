"""Generate an isolated observer key; accept only explicitly pinned host keys."""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
from pathlib import Path
import re
import stat
import struct
from uuid import uuid4

from homeassistant.core import HomeAssistant

from .backends.gateway_ssh import GatewayTransportError
from .const import DOMAIN


def validate_host_key(value: str) -> str:
    """An Ed25519 public host key, never a fingerprint or automatic TOFU."""
    if not isinstance(value, str) or "\n" in value or "\r" in value:
        raise ValueError("Invalid gateway host key")
    fields = value.strip().split()
    if len(fields) != 2 or fields[0] != "ssh-ed25519":
        raise ValueError("An Ed25519 gateway host key is required")
    try:
        raw = base64.b64decode(fields[1], validate=True)
        algorithm_size = struct.unpack(">I", raw[:4])[0]
        end = 4 + algorithm_size
        key_size = struct.unpack(">I", raw[end : end + 4])[0]
        if raw[4:end] != b"ssh-ed25519" or key_size != 32 or len(raw) != end + 4 + key_size:
            raise ValueError("Invalid gateway host key")
    except (binascii.Error, struct.error):
        raise ValueError("Invalid gateway host key") from None
    return " ".join(fields)


def same_pinned_gateway(first: Path, second: Path) -> bool:
    """Executor-only equality of already validated dedicated known-host files."""
    keys = []
    for path in (first, second):
        with path.open("rb") as source:
            content = source.read(4097)
        if len(content) > 4096:
            raise GatewayTransportError("Invalid gateway trust material")
        lines = content.decode("ascii").splitlines()
        if not 1 <= len(lines) <= 2 or any(len(line.split()) != 3 for line in lines):
            raise GatewayTransportError("Invalid gateway trust material")
        if len({line.split()[0] for line in lines}) != len(lines):
            raise GatewayTransportError("Ambiguous gateway trust material")
        pinned = {validate_host_key(" ".join(line.split()[1:])) for line in lines}
        if len(pinned) != 1:
            raise GatewayTransportError("Ambiguous gateway trust material")
        keys.append(pinned.pop())
    return keys[0] == keys[1]


def credential_directory(hass: HomeAssistant, key_id: str) -> Path:
    if not isinstance(key_id, str) or not re.fullmatch("[a-f0-9]{32}", key_id):
        raise GatewayTransportError("Invalid gateway credential identity")
    return Path(hass.config.path(DOMAIN, "gateway", key_id))


def _prepare_directory(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise GatewayTransportError("Unsafe gateway credential directory")


def _check_private_key(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise GatewayTransportError("Unsafe gateway private key")
    return True


async def _keygen(*arguments: str) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/ssh-keygen",
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except OSError:
        raise GatewayTransportError("Native SSH key generator is unavailable") from None
    try:
        async with asyncio.timeout(5):
            output = await process.stdout.read(8193)
            if len(output) > 8192:
                raise GatewayTransportError("Unexpected SSH key generator response")
            await process.wait()
        if process.returncode != 0:
            raise GatewayTransportError("Unable to prepare gateway public key")
        return output
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def prepare_observer_key(hass: HomeAssistant, key_id: str) -> str:
    """Create a dedicated key only after the user selects the observer feature."""
    return await _prepare_gateway_key(hass, key_id, "observer")


async def prepare_ipv6_actor_key(hass: HomeAssistant, key_id: str) -> str:
    """The policy writer receives its own non-interchangeable key."""
    return await _prepare_gateway_key(hass, key_id, "ipv6_actor")


async def _prepare_gateway_key(hass: HomeAssistant, key_id: str, profile: str) -> str:
    if profile not in ("observer", "ipv6_actor"):
        raise GatewayTransportError("Invalid gateway key profile")
    directory = credential_directory(hass, key_id)
    await hass.async_add_executor_job(_prepare_directory, directory)
    key = directory / f"{profile}_ed25519"
    if not await hass.async_add_executor_job(_check_private_key, key):
        staging = directory / (".key-" + uuid4().hex)
        try:
            await _keygen(
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                f"service-route-sync-{profile}",
                "-f",
                str(staging),
            )
            await hass.async_add_executor_job(os.replace, staging, key)
        finally:
            for path in (staging, Path(str(staging) + ".pub")):
                await hass.async_add_executor_job(path.unlink, True)
    # Derive the public key from the private key, avoiding stale/tampered .pub.
    public = (await _keygen("-y", "-f", str(key))).decode("ascii").strip()
    return validate_host_key(" ".join(public.split()[:2]))


def save_known_host(directory: Path, host: str, public_key: str) -> None:
    """Executor-only atomic update; never discover or accept keys automatically."""
    from .backends.gateway_ssh import GatewaySSHConfig

    GatewaySSHConfig(host, directory)
    _prepare_directory(directory)
    public_key = validate_host_key(public_key)
    path = directory / "known_hosts"
    staging = directory / (".known_hosts-" + uuid4().hex)
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(f"{host} {public_key}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)


def bind_existing_gateway_host(observer: Path, trusted: Path, host: str) -> bool:
    """Add a LAN address only when both existing scopes pin the same host key.

    Return True if a new mapping was written. The old WAN mapping stays valid
    until the options flow has completed its live read-only handshake.
    """
    from .backends.gateway_ssh import GatewaySSHConfig

    GatewaySSHConfig(host, observer).validate_files()
    GatewaySSHConfig(host, trusted).validate_files()
    def pinned_lines(path: Path) -> list[str]:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o022 or info.st_nlink != 1):
                raise GatewayTransportError("Unsafe gateway trust material")
            content = stream.read(4097)
        if len(content) > 4096:
            raise GatewayTransportError("Invalid gateway trust material")
        lines = content.decode("ascii").splitlines()
        if not 1 <= len(lines) <= 2 or any(len(line.split()) != 3 for line in lines):
            raise GatewayTransportError("Invalid gateway trust material")
        keys = {validate_host_key(' '.join(line.split()[1:])) for line in lines}
        if len(keys) != 1 or len({line.split()[0] for line in lines}) != len(lines):
            raise GatewayTransportError("Ambiguous gateway trust material")
        return lines

    old = pinned_lines(observer / "known_hosts")
    source_lines = pinned_lines(trusted / "known_hosts")
    if len(source_lines) != 1:
        raise GatewayTransportError("Ambiguous management identity")
    source = source_lines[0]
    if validate_host_key(' '.join(old[0].split()[1:])) != validate_host_key(' '.join(source.split()[1:])):
        raise GatewayTransportError("Gateway identity mismatch")
    if source.split()[0] != host:
        raise GatewayTransportError("Gateway management address mismatch")
    if any(line.split()[0] == host for line in old):
        return False
    if len(old) != 1:
        raise GatewayTransportError("Gateway trust material is full")
    path = observer / "known_hosts"
    staging = observer / (".known_hosts-" + uuid4().hex)
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(old[0] + "\n" + source + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
        dirfd = os.open(observer, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
        return True
    finally:
        staging.unlink(missing_ok=True)
