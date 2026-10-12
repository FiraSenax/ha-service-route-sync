"""Public, bounded feed retrieval and explicit service selection."""

import asyncio
import re
from time import monotonic
from urllib.parse import urlparse

import aiohttp

from .catalog import CATALOG
from .core import SyncError, normalize_prefixes

MAX_BYTES = 20_000_000


def select_prefixes(service_id, payload, options):
    service = CATALOG[service_id]
    if service.provider == "ripe":
        return [item["prefix"] for item in payload["data"]["prefixes"]]
    if service.provider == "m365":
        areas = {"Common", service.selector}
        return [
            prefix
            for item in payload
            if (service.selector == "all" or item.get("serviceArea") in areas)
            and item.get("category") in options.get("m365_categories", ["Optimize"])
            for prefix in item.get("ips", [])
        ]
    if service.provider == "azure":
        tags = set(options["azure_tags"])
        rows = {item["name"]: item for item in payload["values"]}
        if tags - rows.keys():
            raise SyncError("One or more Azure service tags are absent from the feed")
        return [
            prefix for tag in sorted(tags) for prefix in rows[tag]["properties"]["addressPrefixes"]
        ]
    if service.provider == "aws":
        regions, services = set(options["aws_regions"]), set(options["aws_services"])
        result = []
        for key, prefix_key in (("prefixes", "ip_prefix"), ("ipv6_prefixes", "ipv6_prefix")):
            result.extend(
                item[prefix_key]
                for item in payload.get(key, [])
                if item.get("region") in regions and item.get("service") in services
            )
        return result
    if service.provider == "aws_fortnite":
        result = ["52.23.121.216/32"]
        for key, prefix_key in (("prefixes", "ip_prefix"), ("ipv6_prefixes", "ipv6_prefix")):
            result.extend(
                item[prefix_key] for item in payload.get(key, [])
                if item.get("region") == "eu-central-1" and item.get("service") == "EC2"
            )
        return result
    return options[f"{service_id}_prefixes"]


class FeedClient:
    def __init__(self, session, options):
        self.session = session
        self.options = options
        self._cycle_cache = {}
        self._cache = {}

    async def _fetch(self, url, *, html=False):
        if url in self._cycle_cache:
            return self._cycle_cache[url]
        cached = self._cache.get(url)
        if cached and monotonic() - cached[0] < 3600:
            self._cycle_cache[url] = cached[1]
            return cached[1]
        host = urlparse(url).hostname
        if host not in {
            "stat.ripe.net",
            "endpoints.office.com",
            "ip-ranges.amazonaws.com",
            "www.microsoft.com",
            "download.microsoft.com",
        }:
            raise SyncError("Unapproved feed host")
        async with self.session.get(
            url, timeout=aiohttp.ClientTimeout(total=45), allow_redirects=False
        ) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    raise SyncError("Feed exceeds size limit")
            if html:
                result = data.decode("utf-8")
            else:
                import json

                result = json.loads(data)
        self._cycle_cache[url] = result
        self._cache[url] = (monotonic(), result)
        return result

    async def prefixes(self, service_id):
        service = CATALOG[service_id]
        if service.provider == "ripe":
            base = "https://stat.ripe.net/data/"
            overview = await self._fetch(
                f"{base}as-overview/data.json?resource=AS{service.selector}"
            )
            holder = re.sub(r"[^A-Z0-9]", "", overview["data"].get("holder", "").upper())
            if service.owner not in holder:
                raise SyncError(f"ASN ownership validation failed for {service.name}")
            payload = await self._fetch(
                f"{base}announced-prefixes/data.json?resource=AS{service.selector}"
            )
        elif service.provider == "m365":
            guid = self.options["client_request_id"]
            payload = await self._fetch(
                f"https://endpoints.office.com/endpoints/Worldwide?ClientRequestId={guid}"
            )
        elif service.provider in ("aws", "aws_fortnite"):
            payload = await self._fetch("https://ip-ranges.amazonaws.com/ip-ranges.json")
        elif service.provider == "azure":
            page = await self._fetch(
                "https://www.microsoft.com/en-us/download/details.aspx?id=56519", html=True
            )
            links = re.findall(
                r'https://download\.microsoft\.com/[^\s"<>]+ServiceTags_Public_[0-9]+\.json', page
            )
            if not links:
                raise SyncError("Official Azure download link not found; existing routes retained")
            payload = await self._fetch(sorted(set(links))[-1])
        else:
            payload = None
        try:
            selected = select_prefixes(service_id, payload, self.options)
            return normalize_prefixes(
                selected,
                ipv6=self.options.get("ipv6", True),
                maximum=self.options.get("maximum_prefixes", 1000),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SyncError(f"Invalid or incomplete feed for {service.name}") from exc

    async def all_prefixes(self):
        # Sequential requests share common feeds and avoid API bursts.
        self._cycle_cache.clear()
        result = {}
        for service_id in self.options["services"]:
            result[service_id] = await self.prefixes(service_id)
            await asyncio.sleep(0)
        return result
