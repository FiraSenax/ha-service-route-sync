"""Inject one controller double into a composed configuration-flow test."""

from contextlib import ExitStack
from importlib import import_module
from unittest.mock import MagicMock, patch


class patch_flow_backend:
    def __init__(self, **kwargs):
        self.backend = MagicMock(**kwargs)
        self.stack = ExitStack()

    def start(self):
        for name in ("setup", "routes", "network", "clients", "wifi"):
            module = import_module(f"custom_components.service_route_sync.flows.{name}")
            self.stack.enter_context(patch.object(module, "UniFiBackend", self.backend))
        return self.backend

    def stop(self):
        self.stack.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()
