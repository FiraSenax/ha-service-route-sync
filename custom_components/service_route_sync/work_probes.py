"""Fixed public HTTPS phase probes; no credentials, redirects or dynamic URLs."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
from types import MappingProxyType

from .backends.gateway_ssh import _read_bounded, _stop
from .network import NetworkDataError, finite_number
from .network_metrics import MetricWriter

TARGETS = MappingProxyType(
    {
        "m365-login": "login.microsoftonline.com",
        "m365-outlook": "outlook.office.com",
        "m365-teams": "teams.microsoft.com",
    }
)
_WRITE_OUT = (
    '{"http_code":"%{http_code}","time_namelookup":%{time_namelookup},'
    '"time_connect":%{time_connect},"time_appconnect":%{time_appconnect},'
    '"time_starttransfer":%{time_starttransfer},"time_total":%{time_total}}'
)


@dataclass(frozen=True, slots=True)
class WorkProbe:
    observed_at: datetime
    transport_ok: bool
    http_status: int
    http_ok: bool
    dns_seconds: float | None = None
    tcp_seconds: float | None = None
    tls_seconds: float | None = None
    ttfb_seconds: float | None = None
    total_seconds: float | None = None


def parse_curl(raw, returncode: int, observed_at: datetime) -> WorkProbe:
    if observed_at.tzinfo is None or not isinstance(raw, dict):
        raise NetworkDataError("Invalid work endpoint observation")
    status = raw.get("http_code")
    if isinstance(status, str) and len(status) == 3 and status.isascii() and status.isdecimal():
        status = int(status)
    if type(status) is not int or not 0 <= status <= 599:
        raise NetworkDataError("Invalid work endpoint HTTP status")
    success = returncode == 0 and status > 0
    if returncode != 0:
        return WorkProbe(observed_at, False, status, False)
    keys = (
        "time_namelookup",
        "time_connect",
        "time_appconnect",
        "time_starttransfer",
        "time_total",
    )
    values = [finite_number(raw.get(key)) for key in keys]
    if any(value is None for value in values) or values != sorted(values):
        raise NetworkDataError("Invalid work endpoint phase measurements")
    dns, connect, tls, first, total = values
    return WorkProbe(
        observed_at,
        success,
        status,
        success and 200 <= status < 400,
        dns,
        connect - dns,
        tls - connect,
        first,
        total,
    )


async def probe_work_endpoint(target: str, now) -> WorkProbe:
    """One bounded curl process, verified TLS and no inherited proxy configuration."""
    if target not in TARGETS:
        raise NetworkDataError("Unexpected work endpoint target")
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/curl",
            "--silent",
            "--output",
            "/dev/null",
            "--connect-timeout",
            "5",
            "--max-time",
            "12",
            "--proto",
            "=https",
            "--write-out",
            _WRITE_OUT,
            "https://" + TARGETS[target] + "/",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
        async with asyncio.timeout(15):
            async with asyncio.TaskGroup() as tasks:
                output = tasks.create_task(_read_bounded(process.stdout, 16384))
                tasks.create_task(_read_bounded(process.stderr, 4096))
                tasks.create_task(process.wait())
        return parse_curl(json.loads(output.result()), process.returncode, now())
    except (OSError, ValueError, TimeoutError, ExceptionGroup):
        return WorkProbe(now(), False, 0, False)
    finally:
        if process is not None:
            await _stop(process)


def render_work_probes(observations, now: datetime) -> str:
    writer = MetricWriter()
    for target, row in observations.items():
        if target not in TARGETS:
            raise NetworkDataError("Unexpected work endpoint target")
        labels = {"target": target, "host": TARGETS[target], "vantage": "house"}
        current = 0 <= (now - row.observed_at).total_seconds() < 150
        writer.add("work_endpoint_fresh", current, **labels)
        if not current:
            continue
        for key in (
            "transport_ok",
            "http_status",
            "http_ok",
            "dns_seconds",
            "tcp_seconds",
            "tls_seconds",
            "ttfb_seconds",
            "total_seconds",
        ):
            writer.add("work_endpoint_" + key, getattr(row, key), **labels)
        writer.add("work_endpoint_timestamp_seconds", row.observed_at.timestamp(), **labels)
    return writer.render()
