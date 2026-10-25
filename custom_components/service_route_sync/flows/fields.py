import voluptuous as vol
from homeassistant.helpers import selector
from ..routing.catalog import CATALOG


def connection_fields(defaults):
    return {
        vol.Optional("unifi_host", default=defaults.get("unifi_host", "")): str,
        vol.Required("unifi_port", default=defaults.get("unifi_port", 443)): vol.All(
            int, vol.Range(min=1, max=65535)
        ),
        vol.Required("unifi_site", default=defaults.get("unifi_site", "default")): str,
        vol.Optional("unifi_api_key", default=""): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Optional("unifi_tls_pin_sha256", default=defaults.get("unifi_tls_pin_sha256", "")): str,
    }


def connection_settings(defaults, values):
    settings = {**defaults, **values}
    settings["unifi_host"] = settings["unifi_host"].strip().lower()
    settings["unifi_tls_pin_sha256"] = (
        settings.get("unifi_tls_pin_sha256", "").replace(":", "").strip().lower()
    )
    settings["unifi_api_key"] = values.get("unifi_api_key") or defaults.get("unifi_api_key", "")
    return settings


def connection_identity(settings):
    return (
        f"{settings['unifi_host']}:{settings['unifi_port']}/{settings['unifi_site']}"
        if settings.get("unifi_host")
        else "standalone-probes"
    )


def ipv6_control_fields(settings):
    fields = {}
    for name in ("enabled", "dry_run", "nat66", "sab", "override"):
        key = f"ipv6_{name}_entity"
        marker = (
            vol.Optional(key, default=settings[key]) if settings.get(key) else vol.Optional(key)
        )
        fields[marker] = selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain="input_select" if name == "override" else "input_boolean"
            )
        )
    for name in ("auto", "primary", "secondary"):
        key = f"ipv6_{name}_option"
        fields[vol.Optional(key, default=settings.get(key, name))] = vol.All(
            str, vol.Length(min=1, max=64)
        )
    return fields


def validate_ipv6_controls(settings):
    entities = [
        settings.get(f"ipv6_{name}_entity")
        for name in ("enabled", "dry_run", "nat66", "sab", "override")
    ]
    if not all(entities) or len(set(entities)) != 5:
        raise ValueError("Select five distinct IPv6 helpers")
    options = [
        settings.get(f"ipv6_{name}_option", name) for name in ("auto", "primary", "secondary")
    ]
    if len(set(options)) != 3:
        raise ValueError("Select distinct IPv6 override values")


def fields(hass, defaults):
    services = [{"value": key, "label": service.name} for key, service in CATALOG.items()]
    return vol.Schema(
        {
            **connection_fields(defaults),
            vol.Required("services", default=defaults.get("services", [])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=services, multiple=True)
            ),
            vol.Optional(
                "network_enabled", default=defaults.get("network_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional(
                "client_monitoring_enabled", default=bool(defaults.get("client_profiles"))
            ): selector.BooleanSelector(),
            vol.Optional(
                "work_probe_enabled", default=defaults.get("work_probe_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional(
                "monitoring_enabled", default=defaults.get("monitoring_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional(
                "wifi_history_enabled", default=defaults.get("wifi_history_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional("ipv6", default=defaults.get("ipv6", True)): bool,
            vol.Optional(
                "network_metrics_enabled", default=defaults.get("network_metrics_enabled", False)
            ): selector.BooleanSelector(),
            vol.Optional(
                "maximum_prefixes", default=defaults.get("maximum_prefixes", 1000)
            ): vol.All(int, vol.Range(min=1, max=10000)),
            vol.Optional("maximum_removal", default=defaults.get("maximum_removal", 50)): vol.All(
                int, vol.Range(min=0, max=100)
            ),
            vol.Optional("interval_hours", default=defaults.get("interval_hours", 24)): vol.All(
                int, vol.Range(min=1, max=168)
            ),
        }
    )
