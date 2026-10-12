"""Dedicated pinned identity and immutable command for Windows observations."""

from .gateway_ssh import GatewaySSHConfig, read_gateway
from ..windows_protocol import cursor_request, parse_windows_response


class WindowsSSHConfig(GatewaySSHConfig):
    def arguments(self):
        return (
            *super().arguments()[:-2],
            f"ha-windows@{self.host}",
            "service-route-sync windows v1",
        )


async def read_windows(config, cursor):
    response = await read_gateway(config, timeout=12, input_data=cursor_request(cursor))
    return parse_windows_response(response)


class WindowsArchiveSSHConfig(WindowsSSHConfig):
    def arguments(self):
        return (*super().arguments()[:-1], "service-route-sync windows v2")


async def read_windows_archive(config, cursor):
    response = await read_gateway(config, timeout=12, input_data=cursor_request(cursor, schema=2))
    if not isinstance(response, dict) or response.get("schema") != 2:
        from ..windows_cursor import WindowsSourceError

        raise WindowsSourceError("Windows archive response version mismatch")
    return parse_windows_response(response)
