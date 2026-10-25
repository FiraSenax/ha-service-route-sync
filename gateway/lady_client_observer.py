#!/usr/bin/python3
"""Separate Lady-only target scope using the verified client probe engine."""

from pathlib import Path
import client_observer as observer
from observer import ObservationError

_load = observer.load_targets


def load_lady(path):
    targets = _load(path)
    if len(targets) != 1:
        raise ObservationError("invalid_lady_target_scope")
    return targets


def main():
    observer.COMMAND = "service-route-sync lady-client v1"
    observer.CONFIG_PATH = Path("/data/service-route-sync/lady-client.json")
    observer.LOCK_PATH = Path("/run/service-route-sync-lady-client.lock")
    observer.load_targets = load_lady
    return observer.main()


if __name__ == "__main__":
    raise SystemExit(main())
