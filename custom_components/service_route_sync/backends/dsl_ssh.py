"""Separate pinned credential scope for fixed DSL-only observations."""

from .gateway_ssh import GatewaySSHConfig


class DSLSSHConfig(GatewaySSHConfig):
    def arguments(self):
        return (*super().arguments()[:-1], "service-route-sync dsl v1")
