"""Integration constants."""

DOMAIN = "service_route_sync"
PLATFORMS = ["sensor", "button", "switch", "binary_sensor"]
DEFAULTS = {
    "ipv6": True,
    "maximum_prefixes": 1000,
    "maximum_removal": 50,
    "interval_hours": 24,
    "automatic_apply": False,
    "network_enabled": False,
    "gateway_observer_enabled": False,
    "network_interval_seconds": 10,
    "network_primary_label": "Primary WAN",
    "network_secondary_label": "Secondary WAN",
}
