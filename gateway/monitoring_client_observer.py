#!/usr/bin/python3
"""Separate Monitoring-only target scope using the verified client probe engine."""

from pathlib import Path
import client_observer as observer
from observer import ObservationError

_load = observer.load_targets


def load_monitoring(path):
    targets = _load(path)
    if len(targets) != 1:
        raise ObservationError("invalid_monitoring_target_scope")
    return targets


def main():
    observer.COMMAND = "service-route-sync monitoring-client v1"
    observer.CONFIG_PATH = Path("/data/service-route-sync/monitoring-client.json")
    observer.LOCK_PATH = Path("/run/service-route-sync-monitoring-client.lock")
    observer.load_targets = load_monitoring
    return observer.main()


if __name__ == "__main__":
    raise SystemExit(main())
