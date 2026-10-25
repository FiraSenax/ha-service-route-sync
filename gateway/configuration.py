"""Read bounded administrator-owned gateway configuration without following links."""

import json
import os
from pathlib import Path
import stat


def read_private_config(path):
    path = Path(path)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o077:
        raise ValueError("unsafe_configuration")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError("unsafe_configuration")
        content = os.read(fd, 16385)
        if len(content) > 16384:
            raise ValueError("invalid_configuration")
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("invalid_configuration")
        return value
    finally:
        os.close(fd)
