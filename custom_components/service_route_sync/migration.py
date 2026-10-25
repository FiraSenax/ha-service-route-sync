"""One-time entry migration; legacy names never enter feature runtime code."""

from pathlib import Path
import json
import os
import re
import shutil
import stat
import logging
import filecmp
from uuid import uuid4

from homeassistant.helpers import entity_registry as er, device_registry as dr

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_LEGACY = "lady"
_CURRENT = "monitoring"


def migrate_settings(values):
    result = {}
    for key, value in values.items():
        target = _CURRENT + key[len(_LEGACY) :] if key.startswith(_LEGACY + "_") else key
        if target in result and result[target] != value:
            raise ValueError("Conflicting configuration namespaces")
        result[target] = value
    # Never resolve or copy another integration's credentials during upgrade.
    result.pop("unifi_entry_id", None)
    return result


def _private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise ValueError("Unsafe migration source")


def _safe_parents(path, root):
    relative = path.parent.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if not current.exists():
            current.mkdir(mode=0o700)
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("Unsafe migration directory")


def _copy_file(source, target, root):
    _private_file(source)
    _safe_parents(target, root)
    if target.is_symlink():
        raise ValueError("Unsafe migration destination")
    if target.exists():
        _private_file(target)
        if not filecmp.cmp(source, target, shallow=False):
            raise ValueError("Conflicting migration destination")
        return
    _safe_parents(target, root)
    temporary = target.with_name(target.name + ".migration-" + uuid4().hex)
    with source.open("rb") as reader, temporary.open("xb") as writer:
        os.chmod(temporary, 0o600)
        shutil.copyfileobj(reader, writer, 65536)
        writer.flush()
        os.fsync(writer.fileno())
    os.replace(temporary, target)


def migrate_storage(config_dir, entry_id):
    """Copy accepted checkpoint and its journal before changing the entry version.

    Old files remain a recovery backup. A retry accepts identical destinations;
    conflicting state fails closed instead of choosing a cursor or history.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", entry_id):
        raise ValueError("Invalid entry identity")
    root = Path(config_dir).resolve()
    old_key, new_key = (f"{DOMAIN}.{name}.{entry_id}" for name in (_LEGACY, _CURRENT))
    old, new = (root / ".storage" / key for key in (old_key, new_key))
    _safe_parents(old, root)
    if old.is_symlink():
        raise ValueError("Unsafe checkpoint")
    if old.exists():
        _private_file(old)
        if old.stat().st_size > 3_000_000:
            raise ValueError("Oversized checkpoint")
        raw = json.loads(old.read_bytes())
        if raw.get("key") != old_key or not isinstance(raw.get("data"), dict):
            raise ValueError("Unexpected checkpoint identity")
        raw["key"] = new_key
        # Validate without loading or advancing a Windows source cursor.
        from .monitoring.monitoring_store import validate_checkpoint

        checkpoint = validate_checkpoint(raw["data"]["checkpoint"])
        revision = raw["data"].get("revision")
        if type(revision) is not int or revision < checkpoint.get("history_revision", 0):
            raise ValueError("Invalid checkpoint revision")
        if checkpoint.get("history_revision", 0):
            journal = (
                root
                / DOMAIN
                / f"{_LEGACY}_history"
                / old_key
                / (checkpoint.get("history_epoch") or "observation")
                / "history.sqlite"
            )
            if not journal.is_file():
                raise ValueError("Accepted history is missing")
        if new.is_symlink():
            raise ValueError("Unsafe checkpoint destination")
        if new.exists():
            _private_file(new)
            if json.loads(new.read_bytes()) != raw:
                raise ValueError("Conflicting checkpoint")
        else:
            temporary = new.with_name(new.name + ".migration-" + uuid4().hex)
            with temporary.open("x") as stream:
                os.chmod(temporary, 0o600)
                json.dump(raw, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, new)
    history = root / DOMAIN / f"{_LEGACY}_history" / old_key
    target = root / DOMAIN / f"{_CURRENT}_history" / new_key
    if history.exists() or history.is_symlink():
        _safe_parents(history / "placeholder", root)
        for directory, directories, files in os.walk(history, followlinks=False):
            parent = Path(directory)
            for path in [parent, *(parent / name for name in directories)]:
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                    raise ValueError("Unsafe history directory")
            for name in files:
                source = parent / name
                _copy_file(source, target / source.relative_to(history), root)


async def async_migrate_entry(hass, entry):
    """Migrate HA-owned state only; external gateway upgrades remain explicit."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        try:
            data, options = migrate_settings(entry.data), migrate_settings(entry.options)
            await hass.async_add_executor_job(migrate_storage, hass.config.path(), entry.entry_id)
            registry = er.async_get(hass)
            old_prefix, new_prefix = (f"{entry.entry_id}_{name}" for name in (_LEGACY, _CURRENT))
            for row in er.async_entries_for_config_entry(registry, entry.entry_id):
                if row.unique_id.startswith(old_prefix + "_"):
                    registry.async_update_entity(
                        row.entity_id, new_unique_id=new_prefix + row.unique_id[len(old_prefix) :]
                    )
            devices = dr.async_get(hass)
            for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
                if (DOMAIN, old_prefix) in device.identifiers:
                    devices.async_update_device(
                        device.id,
                        new_identifiers=(device.identifiers - {(DOMAIN, old_prefix)})
                        | {(DOMAIN, new_prefix)},
                    )
            # Gateway protocol and control-helper settings changed since schema 1.
            # Reads can retry, but upgrades never activate network writes or delivery.
            options.update(automatic_apply=False, ipv6_actor_active=False, monitoring_active=False)
            hass.config_entries.async_update_entry(
                entry, data=data, options=options, version=2, minor_version=1
            )
        except (OSError, ValueError, KeyError, TypeError):
            _LOGGER.error("Entry migration could not preserve configuration and history")
            return False
    else:
        hass.config_entries.async_update_entry(entry, minor_version=1)
    return True
