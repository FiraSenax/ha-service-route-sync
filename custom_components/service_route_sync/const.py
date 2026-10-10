"""Integration constants."""

DOMAIN = "service_route_sync"
PLATFORMS = ["sensor", "button", "switch"]
DEFAULTS = {
    "ipv6": True,
    "maximum_prefixes": 1000,
    "maximum_removal": 50,
    "interval_hours": 24,
    "automatic_apply": False,
}
