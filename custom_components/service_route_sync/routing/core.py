"""Router-independent planning and transactional write safeguards."""

from copy import deepcopy
from dataclasses import dataclass
import ipaddress
from typing import Protocol


class SyncError(Exception):
    """A source or router state is unsafe to apply."""


def normalize_prefixes(values, *, ipv6=True, maximum=1000):
    networks = set()
    for value in values:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except (ValueError, TypeError) as exc:
            raise SyncError("Invalid destination prefix") from exc
        if (
            network.prefixlen == 0
            or not network.is_global
            or network.is_multicast
            or network.is_reserved
        ):
            raise SyncError("Default routes and non-public prefixes are not allowed")
        if ipv6 or network.version == 4:
            networks.add(network)
    if not networks:
        raise SyncError("No usable destination prefixes; existing route retained")
    # Collapse only within the same IP version; do not broaden network coverage.
    collapsed = []
    for version in (4, 6):
        collapsed.extend(ipaddress.collapse_addresses(n for n in networks if n.version == version))
    if len(collapsed) > maximum:
        raise SyncError("Prefix limit exceeded; never truncate a feed")
    return tuple(
        str(n)
        for n in sorted(collapsed, key=lambda n: (n.version, int(n.network_address), n.prefixlen))
    )


class Backend(Protocol):
    async def read_routes(self) -> dict[str, dict]: ...
    async def write_route(self, route: dict) -> None: ...


@dataclass
class Plan:
    service: str
    route_id: str
    before: dict
    after: dict
    added: int
    removed: int
    removed_coverage_pct: float
    added_coverage_pct: float

    @property
    def changed(self):
        return bool(self.added_coverage_pct or self.removed_coverage_pct)


def route_prefixes(route):
    entries = route.get("ip_addresses") or []
    if any(x.get("ports") or x.get("port_ranges") for x in entries):
        raise SyncError("Port-specific destination entries are not supported")
    try:
        return {str(ipaddress.ip_network(x["ip_or_subnet"], strict=False)) for x in entries}
    except (KeyError, TypeError, ValueError) as exc:
        raise SyncError("Unexpected router destination format") from exc


def removed_coverage_percentage(previous, desired):
    """Compare actual address coverage, not CIDR spelling after safe collapse."""
    largest = 0.0
    for version in (4, 6):
        old = list(
            ipaddress.collapse_addresses(
                ipaddress.ip_network(value)
                for value in previous
                if ipaddress.ip_network(value).version == version
            )
        )
        if not old:
            continue
        remaining = old
        covers = ipaddress.collapse_addresses(
            ipaddress.ip_network(value)
            for value in desired
            if ipaddress.ip_network(value).version == version
        )
        for cover in covers:
            next_remaining = []
            for portion in remaining:
                if not portion.overlaps(cover):
                    next_remaining.append(portion)
                elif cover.subnet_of(portion):
                    next_remaining.extend(portion.address_exclude(cover))
                # A portion fully inside the new feed has no removed coverage.
            if len(next_remaining) > 20_000:
                raise SyncError("Prefix coverage comparison exceeds safety bound")
            remaining = next_remaining
        removed_addresses = sum(row.num_addresses for row in remaining)
        old_addresses = sum(row.num_addresses for row in old)
        largest = max(largest, 100 * removed_addresses / old_addresses)
    return largest


def build_plan(service, route, prefixes, *, maximum_removal=0.5):
    if route.get("matching_target") != "IP" or route.get("domains") or route.get("regions"):
        raise SyncError("Bind an IP-based route without domain/region matching")
    before = deepcopy(route)
    previous = route_prefixes(before)
    desired = set(prefixes)
    if not desired:
        raise SyncError("Empty feed cannot replace a route")
    removed = previous - desired
    removed_coverage_pct = removed_coverage_percentage(previous, desired)
    added_coverage_pct = removed_coverage_percentage(desired, previous)
    if previous and removed_coverage_pct > maximum_removal * 100:
        raise SyncError("Removal exceeds configured limit; inspect the preview")
    after = deepcopy(before)
    after["ip_addresses"] = [
        {
            "ip_or_subnet": value,
            "ip_version": "v6" if ":" in value else "v4",
            "ports": [],
            "port_ranges": [],
        }
        for value in prefixes
    ]
    return Plan(
        service,
        str(before["_id"]),
        before,
        after,
        len(desired - previous),
        len(removed),
        removed_coverage_pct,
        added_coverage_pct,
    )


def policy(route):
    """Fields we must never change or silently restore over concurrent edits."""
    return {
        key: deepcopy(value)
        for key, value in route.items()
        if key not in ("ip_addresses", "modified", "updated_at", "last_updated")
    }


async def apply_plan(backend, plan, save_backup):
    current = (await backend.read_routes()).get(plan.route_id)
    if current is None or current != plan.before:
        raise SyncError("Route changed since preview; no write performed")
    await save_backup(plan)
    await backend.write_route(deepcopy(plan.after))
    actual = (await backend.read_routes()).get(plan.route_id)
    if (
        actual is None
        or route_prefixes(actual) != route_prefixes(plan.after)
        or policy(actual) != policy(plan.after)
    ):
        # Never retry blindly: the write may already have applied. Surface this
        # as a failed verification and let the operator inspect/restore safely.
        raise SyncError("Router write could not be verified; no automatic retry")


async def restore_backup(backend, backup):
    current = (await backend.read_routes()).get(backup["route_id"])
    if (
        current is None
        or policy(current) != policy(backup["after"])
        or route_prefixes(current) != route_prefixes(backup["after"])
    ):
        raise SyncError("Route changed after sync; restore would overwrite another edit")
    restored = deepcopy(current)
    restored["ip_addresses"] = deepcopy(backup["before"].get("ip_addresses", []))
    await backend.write_route(restored)
    actual = (await backend.read_routes()).get(backup["route_id"])
    if (
        actual is None
        or route_prefixes(actual) != route_prefixes(restored)
        or policy(actual) != policy(restored)
    ):
        raise SyncError("Restoration could not be verified")


def build_plans(routes, prefixes, bindings, *, maximum_removal=1.0):
    """Validate the complete cycle before callers can apply any route."""
    if set(bindings) != set(prefixes) or len(set(bindings.values())) != len(bindings):
        raise SyncError("Every selected service must have its own route")
    plans = []
    for service, binding in bindings.items():
        if binding not in routes:
            raise SyncError("A bound route no longer exists")
        plans.append(
            build_plan(service, routes[binding], prefixes[service], maximum_removal=maximum_removal)
        )
    return plans
