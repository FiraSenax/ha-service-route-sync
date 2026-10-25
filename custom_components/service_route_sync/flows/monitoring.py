import voluptuous as vol


class MonitoringSteps:
    async def async_step_monitoring_mode(self, user_input=None):
        """Enable notifications for the explicitly selected service."""
        if not self.defaults.get("monitoring_enabled"):
            return self.async_abort(reason="monitoring_not_configured")
        schema = vol.Schema(
            {
                vol.Required(
                    "monitoring_active", default=self.defaults.get("monitoring_active", False)
                ): bool
            }
        )
        errors = {}
        if user_input is not None:
            try:
                values = schema(user_input)
                if values["monitoring_active"] and not self.defaults.get(
                    "monitoring_notify_service"
                ):
                    errors["base"] = "notification_target_required"
                else:
                    self.settings = {**self.defaults, **values}
                    return self.finish()
            except vol.Invalid:
                errors["base"] = "invalid_configuration"
        return self.async_show_form(step_id="monitoring_mode", data_schema=schema, errors=errors)
