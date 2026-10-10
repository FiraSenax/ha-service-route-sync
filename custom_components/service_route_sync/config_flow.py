"""Explicit service and existing route selection; writes disabled by default."""

from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .backends.unifi import UniFiBackend
from .catalog import CATALOG
from .const import DEFAULTS, DOMAIN
from .core import SyncError, normalize_prefixes


def fields(hass, defaults):
    routers = [
        {"value": entry.entry_id, "label": entry.title}
        for entry in hass.config_entries.async_entries("unifi")
    ]
    services = [{"value": key, "label": service.name} for key, service in CATALOG.items()]
    return vol.Schema(
        {
            vol.Required(
                "unifi_entry_id", default=defaults.get("unifi_entry_id", "")
            ): selector.SelectSelector(selector.SelectSelectorConfig(options=routers)),
            vol.Required("services", default=defaults.get("services", [])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=services, multiple=True)
            ),
            vol.Optional("ipv6", default=defaults.get("ipv6", True)): bool,
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


class Steps:
    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            self.settings = {**self.defaults, **user_input}
            self.settings.setdefault("client_request_id", str(uuid4()))
            if isinstance(self, ConfigFlow):
                await self.async_set_unique_id(user_input["unifi_entry_id"])
                self._abort_if_unique_id_configured()
            elif user_input["unifi_entry_id"] != self.defaults["unifi_entry_id"]:
                errors["base"] = "immutable_router"
            if not user_input["services"]:
                errors["base"] = "no_services"
            elif not errors:
                return await self.async_step_sources()
        return self.async_show_form(
            step_id="user", data_schema=fields(self.hass, self.defaults), errors=errors
        )

    async def async_step_sources(self, user_input=None):
        schema = {}
        selected = self.settings["services"]
        wanted = []
        if any(CATALOG[key].provider == "m365" for key in selected):
            wanted.append("m365_categories")
            self.settings.setdefault("m365_categories", ["Optimize"])
        if "azure" in selected:
            wanted.append("azure_tags")
        if "aws" in selected:
            wanted.extend(("aws_regions", "aws_services"))
        wanted.extend(f"{key}_prefixes" for key in ("sap", "custom") if key in selected)
        if not wanted:
            return await self.async_step_routes()
        for key in wanted:
            if key == "m365_categories":
                schema[vol.Required(key, default=self.settings[key])] = selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["Optimize", "Allow", "Default"], multiple=True
                    )
                )
                continue
            schema[vol.Required(key, default=", ".join(self.settings.get(key, [])))] = str
        errors = {}
        if user_input is not None:
            try:
                for key in wanted:
                    raw = user_input[key]
                    values = (
                        raw
                        if isinstance(raw, list)
                        else [
                            part.strip()
                            for part in user_input[key].replace("\n", ",").split(",")
                            if part.strip()
                        ]
                    )
                    if not values:
                        raise SyncError("Empty selection")
                    if key.endswith("_prefixes"):
                        values = list(
                            normalize_prefixes(values, maximum=self.settings["maximum_prefixes"])
                        )
                    self.settings[key] = values
                return await self.async_step_routes()
            except SyncError:
                errors["base"] = "invalid_sources"
        return self.async_show_form(
            step_id="sources", data_schema=vol.Schema(schema), errors=errors
        )

    async def async_step_routes(self, user_input=None):
        errors = {}
        try:
            routes = await UniFiBackend(self.hass, self.settings["unifi_entry_id"]).read_routes()
        except Exception:  # Authentication/API failures must keep the flow read-only.
            return self.async_abort(reason="router_unavailable")
        eligible = {
            key: route
            for key, route in routes.items()
            if route.get("matching_target") == "IP"
            and not route.get("domains")
            and not route.get("regions")
        }
        if not eligible:
            return self.async_abort(reason="no_routes")
        choices = [
            {"value": key, "label": route.get("description") or key}
            for key, route in eligible.items()
        ]
        schema = {}
        for key in self.settings["services"]:
            binding = self.settings.get("bindings", {}).get(key)
            field = vol.Required(key, default=binding) if binding in eligible else vol.Required(key)
            schema[field] = selector.SelectSelector(selector.SelectSelectorConfig(options=choices))
        if user_input is not None:
            if len(set(user_input.values())) != len(user_input):
                errors["base"] = "duplicate_routes"
            elif set(user_input) != set(self.settings["services"]) or any(
                value not in eligible for value in user_input.values()
            ):
                errors["base"] = "invalid_routes"
            else:
                self.settings["bindings"] = dict(user_input)
                return self.finish()
        return self.async_show_form(
            step_id="routes",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "services": ", ".join(CATALOG[key].name for key in self.settings["services"])
            },
        )


class ConfigFlow(Steps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self.defaults = dict(DEFAULTS)

    def finish(self):
        return self.async_create_entry(title="Service Route Sync", data=self.settings)

    @staticmethod
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(Steps, config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        self.defaults = {**self.config_entry.data, **self.config_entry.options}
        return await self.async_step_user(user_input)

    def finish(self):
        self.settings["automatic_apply"] = False
        return self.async_create_entry(title="", data=self.settings)
