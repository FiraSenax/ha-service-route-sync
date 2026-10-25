"""Home Assistant entry point composing independent feature setup flows."""

from homeassistant import config_entries
from .const import DEFAULTS, DOMAIN
from .monitoring.monitoring_flow import MonitoringFlowSteps
from .frigate.frigate_flow import FrigateFlowSteps
from .flows.routes import RoutesSteps
from .flows.network import NetworkSteps
from .flows.ipv6 import Ipv6Steps
from .flows.clients import ClientsSteps
from .flows.monitoring import MonitoringSteps
from .flows.wifi import WifiSteps
from .flows.setup import SetupSteps


class Steps(
    RoutesSteps,
    NetworkSteps,
    Ipv6Steps,
    ClientsSteps,
    MonitoringSteps,
    WifiSteps,
    SetupSteps,
    MonitoringFlowSteps,
):
    """Compose the explicit setup path without merging feature implementations."""


class ConfigFlow(Steps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2
    MINOR_VERSION = 1

    def __init__(self):
        self.defaults = dict(DEFAULTS)

    def finish(self):
        return self.async_create_entry(title="Service Route Sync", data=self.settings)

    @staticmethod
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(FrigateFlowSteps, Steps, config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        self.defaults = {**DEFAULTS, **self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            return await self.async_step_user(user_input)
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "user",
                "route_services",
                "route_tls",
                "gateway_management",
                "ipv6_policy",
                "wifi_history",
                "frigate_actor",
                "monitoring_mode",
                "monitoring_dns",
            ],
        )

    def finish(self):
        # Only route-policy changes require a fresh manual enablement. Editing
        # an independent IPv6, Monitoring, Frigate or Wi-Fi feature must not silently
        # stop the already reviewed daily route updates.
        route_fields = (
            "services",
            "bindings",
            "m365_categories",
            "azure_tags",
            "aws_regions",
            "aws_services",
            "sap_prefixes",
            "custom_prefixes",
            "maximum_removal",
            "maximum_prefixes",
            "ipv6",
            "interval_hours",
            "unifi_tls_pin_sha256",
            "unifi_route_api_key",
            "unifi_host",
            "unifi_port",
            "unifi_site",
            "unifi_api_key",
        )
        if any(self.settings.get(key) != self.defaults.get(key) for key in route_fields):
            self.settings["automatic_apply"] = False
        return self.async_create_entry(title="", data=self.settings)
