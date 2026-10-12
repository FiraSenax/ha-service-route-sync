"""A separate pinned SSH identity for the fixed client observation command."""

from .gateway_ssh import GatewaySSHConfig, read_gateway
import json


class ClientProbeSSHConfig(GatewaySSHConfig):
    def arguments(self) -> tuple[str, ...]:
        # Reuse the hardened transport policy; only this fixed command differs.
        return (*super().arguments()[:-1], "service-route-sync clients v1")


async def read_client_probes(config: ClientProbeSSHConfig, *, addresses=None):
    """Allow the gateway's bounded scan plus connection setup; no custom command."""
    if addresses is None:
        return await read_gateway(config, timeout=62)
    frame = json.dumps({"schema": 1, "addresses": addresses}, separators=(",", ":")).encode()
    return await read_gateway(config, timeout=62, input_data=frame)
