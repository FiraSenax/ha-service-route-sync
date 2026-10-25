import re
import voluptuous as vol
from homeassistant.helpers import selector
from ..backends.unifi import UniFiBackend, read_controller
from ..routing.catalog import CATALOG
from ..routing.core import SyncError, normalize_prefixes


class RoutesSteps:
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
        if not self.settings["services"]:
            return (
                self.finish()
                if getattr(self, "_route_only", False)
                else await self.async_step_network()
            )
        errors = {}
        try:
            routes = await read_controller(
                self.hass, self.settings, "read_routes", backend_factory=UniFiBackend
            )
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
                return (
                    self.finish()
                    if getattr(self, "_route_only", False)
                    else await self.async_step_network()
                )
        return self.async_show_form(
            step_id="routes",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "services": ", ".join(CATALOG[key].name for key in self.settings["services"])
            },
        )

    async def async_step_route_tls(self, user_input=None):
        """Pin the exact UniFi certificate before this integration may write routes."""
        schema = vol.Schema(
            {
                vol.Required(
                    "unifi_tls_pin_sha256", default=self.defaults.get("unifi_tls_pin_sha256", "")
                ): selector.TextSelector(),
                vol.Optional("unifi_route_api_key"): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        errors = {}
        if user_input is not None:
            backend = None
            try:
                values = schema(user_input)
                pin = values["unifi_tls_pin_sha256"].replace(":", "").lower().strip()
                if not re.fullmatch(r"[0-9a-f]{64}", pin):
                    raise ValueError("Invalid SHA-256 fingerprint")
                api_key = values.get("unifi_route_api_key") or self.defaults.get(
                    "unifi_route_api_key"
                )
                backend = UniFiBackend(
                    self.hass,
                    {**self.defaults, "unifi_tls_pin_sha256": pin, "unifi_route_api_key": api_key},
                )
                routes = await backend.read_routes()
                if any(
                    route_id not in routes
                    for route_id in self.defaults.get("bindings", {}).values()
                ):
                    raise ValueError("A selected UniFi route is missing")
                self.settings = {**self.defaults, "unifi_tls_pin_sha256": pin}
                if api_key:
                    self.settings["unifi_route_api_key"] = api_key
                return self.finish()
            except (vol.Invalid, ValueError, KeyError, OSError, SyncError, TimeoutError):
                errors["base"] = "invalid_configuration"
            finally:
                if backend is not None:
                    await backend.async_close()
        return self.async_show_form(step_id="route_tls", data_schema=schema, errors=errors)

    async def async_step_route_services(self, user_input=None):
        """Edit route sources and bindings without reprovisioning network or actors."""
        schema = vol.Schema(
            {
                vol.Required(
                    "services", default=self.defaults.get("services", [])
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": key, "label": service.name}
                            for key, service in CATALOG.items()
                        ],
                        multiple=True,
                    )
                ),
            }
        )
        if user_input is None:
            return self.async_show_form(step_id="route_services", data_schema=schema)
        try:
            values = schema(user_input)
            chosen = values["services"]
            if len(chosen) != len(set(chosen)) or any(key not in CATALOG for key in chosen):
                raise vol.Invalid("Invalid route service selection")
            self.settings = {**self.defaults, "services": chosen}
            self._route_only = True
            return await self.async_step_sources()
        except vol.Invalid:
            return self.async_show_form(
                step_id="route_services",
                data_schema=schema,
                errors={"base": "invalid_configuration"},
            )
