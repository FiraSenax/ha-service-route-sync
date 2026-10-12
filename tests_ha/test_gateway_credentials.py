"""Real local key preparation and strict pinning without gateway connections."""

import base64
import struct
import tempfile
import unittest

from homeassistant.core import HomeAssistant
from custom_components.service_route_sync.gateway_credentials import (
    credential_directory,
    prepare_observer_key,
    save_known_host,
    validate_host_key,
    bind_existing_gateway_host,
    same_pinned_gateway,
)
from custom_components.service_route_sync.backends.gateway_ssh import (
    GatewaySSHConfig,
    GatewayTransportError,
)

PUBLIC = (
    "ssh-ed25519 "
    + base64.b64encode(
        struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))
    ).decode()
)


class CredentialTests(unittest.IsolatedAsyncioTestCase):
    def test_two_address_observer_pin_matches_dedicated_actor_pin(self):
        with tempfile.TemporaryDirectory() as root:
            from pathlib import Path
            observer, actor = Path(root) / 'observer', Path(root) / 'actor'
            observer.mkdir(mode=0o700)
            actor.mkdir(mode=0o700)
            (observer / 'known_hosts').write_text(
                '192.0.2.1 ' + PUBLIC + '\n192.168.1.1 ' + PUBLIC + '\n')
            (actor / 'known_hosts').write_text('192.168.1.1 ' + PUBLIC + '\n')
            self.assertTrue(same_pinned_gateway(observer / 'known_hosts', actor / 'known_hosts'))
            (observer / 'known_hosts').write_text(
                '192.0.2.1 ' + PUBLIC + '\n192.168.1.1 ' + PUBLIC.replace('ssh-ed25519 ', 'ssh-rsa ') + '\n')
            with self.assertRaises(ValueError):
                same_pinned_gateway(observer / 'known_hosts', actor / 'known_hosts')

    def test_lan_binding_keeps_original_pin_and_rejects_identity_change(self):
        with tempfile.TemporaryDirectory() as root:
            from pathlib import Path
            observer, trusted = Path(root) / 'observer', Path(root) / 'client'
            for directory in (observer, trusted):
                directory.mkdir(mode=0o700)
                (directory / 'observer_ed25519').write_text('test-private-key')
                (directory / 'observer_ed25519').chmod(0o600)
            save_known_host(observer, '192.0.2.1', PUBLIC)
            changed = 'ssh-ed25519 ' + base64.b64encode(
                struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(1, 33))
            ).decode()
            save_known_host(trusted, '192.168.1.1', changed)
            with self.assertRaises(GatewayTransportError):
                bind_existing_gateway_host(observer, trusted, '192.168.1.1')
            self.assertEqual(len((observer / 'known_hosts').read_text().splitlines()), 1)
            save_known_host(trusted, '192.168.1.1', PUBLIC)
            self.assertTrue(bind_existing_gateway_host(observer, trusted, '192.168.1.1'))
            self.assertFalse(bind_existing_gateway_host(observer, trusted, '192.168.1.1'))
            self.assertEqual(len((observer / 'known_hosts').read_text().splitlines()), 2)

    def test_untrusted_formats_are_rejected(self):
        for value in (
            "",
            "SHA256:fingerprint",
            PUBLIC + "\nother",
            PUBLIC + " comment",
            "ssh-ed25519 invalid",
            "ssh-rsa " + PUBLIC.split()[1],
            "ssh-ed25519 " + base64.b64encode(b"wrong").decode(),
        ):
            with self.assertRaises(ValueError):
                validate_host_key(value)
        self.assertEqual(validate_host_key(PUBLIC), PUBLIC)

    async def test_native_key_generation_reuses_key_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            try:
                public = await prepare_observer_key(hass, "a" * 32)
                self.assertEqual(await prepare_observer_key(hass, "a" * 32), public)
                path = credential_directory(hass, "a" * 32)
                self.assertEqual((path / "observer_ed25519").stat().st_mode & 0o777, 0o600)
                await hass.async_add_executor_job(save_known_host, path, "192.168.1.1", PUBLIC)
                GatewaySSHConfig("192.168.1.1", path).validate_files()
                self.assertEqual((path / "known_hosts").read_text(), "192.168.1.1 " + PUBLIC + "\n")
                self.assertFalse(list(path.glob(".key-*")))
                for value in ("../outside", "a" * 31, "A" * 32):
                    with self.assertRaises(GatewayTransportError):
                        credential_directory(hass, value)
            finally:
                await hass.async_stop(force=True)
