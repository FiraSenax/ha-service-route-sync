"""Select only already provisioned private Frigate credential scopes."""

import base64
import hashlib
import os
from pathlib import Path
import re
import stat

import voluptuous as vol
from homeassistant.helpers import selector

from ..backends.frigate_ssh import FrigateStatusSSHConfig, read_frigate_status
from ..backends.gateway_ssh import GatewayTransportError


def discover_frigate_scopes(root):
    root = Path(root)
    if not root.exists():
        return {}
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise GatewayTransportError("Unsafe Frigate scope directory")
    choices = {}
    for directory in sorted(root.iterdir())[:32]:
        if not re.fullmatch("[0-9a-f]{32}", directory.name):
            continue
        try:
            info = directory.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                continue
            fd = os.open(directory / "known_hosts", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077
                    or info.st_nlink != 1
                ):
                    continue
                data = source.read(4097)
            if len(data) > 4096:
                continue
            fields = data.decode("ascii").split()
            if len(fields) != 3:
                continue
            config = FrigateStatusSSHConfig(fields[0], directory)
            config.validate_files()
            fingerprint = (
                base64.b64encode(
                    hashlib.sha256(base64.b64decode(fields[2], validate=True)).digest()
                )
                .decode()
                .rstrip("=")
            )
            choices[directory.name] = {
                "host": config.host,
                "label": f"{config.host} · SHA256:{fingerprint}",
            }
        except (OSError, ValueError, UnicodeError, GatewayTransportError):
            continue
    return choices


class FrigateFlowSteps:
    async def async_step_frigate_actor(self, user_input=None):
        # Disabling remains possible even with lost credentials or an offline peer.
        if user_input == {"frigate_actor_enabled": False}:
            self.settings = {
                **self.defaults,
                "frigate_actor_enabled": False,
                "frigate_restart_enabled": False,
            }
            return self.finish()
        try:
            choices = await self.hass.async_add_executor_job(
                discover_frigate_scopes, self.hass.config.path("frigate-restart-credentials")
            )
        except (OSError, GatewayTransportError):
            choices = {}
        schema = {
            vol.Required(
                "frigate_actor_enabled", default=self.defaults.get("frigate_actor_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional(
                "frigate_restart_enabled",
                default=self.defaults.get("frigate_restart_enabled", False),
            ): selector.BooleanSelector(),
        }
        if choices:
            selected = self.defaults.get("frigate_actor_key_id")
            default = selected if selected in choices else next(iter(choices))
            schema[vol.Optional("frigate_actor_key_id", default=default)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": key, "label": value["label"]} for key, value in choices.items()
                    ]
                )
            )
        schema = vol.Schema(schema)
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                if not values["frigate_actor_enabled"]:
                    self.settings = {
                        **self.defaults,
                        "frigate_actor_enabled": False,
                        "frigate_restart_enabled": False,
                    }
                    return self.finish()
                identifier = values.get("frigate_actor_key_id")
                if identifier not in choices:
                    raise vol.Invalid("No provisioned Frigate scope")
                host = choices[identifier]["host"]
                same_binding = identifier == self.defaults.get(
                    "frigate_actor_key_id"
                ) and host == self.defaults.get("frigate_actor_host")
                if values["frigate_restart_enabled"] and not same_binding:
                    errors["base"] = "frigate_observe_first"
                else:
                    config = FrigateStatusSSHConfig(
                        host, Path(self.hass.config.path("frigate-restart-credentials", identifier))
                    )
                    await self.hass.async_add_executor_job(config.validate_files)
                    await read_frigate_status(config)
                    self.settings = {**self.defaults, **values, "frigate_actor_host": host}
                    return self.finish()
            except (vol.Invalid, OSError, ValueError, GatewayTransportError):
                errors["base"] = "invalid_configuration"
        return self.async_show_form(step_id="frigate_actor", data_schema=schema, errors=errors)
