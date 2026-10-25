"""Explicit dependencies on the configured controller, shared by setup and runtime."""


def requires_controller(settings):
    return bool(
        settings.get("services")
        or settings.get("client_profiles")
        or any(
            settings.get(key)
            for key in (
                "network_enabled",
                "client_monitoring_enabled",
                "monitoring_enabled",
                "wifi_history_enabled",
            )
        )
    )
