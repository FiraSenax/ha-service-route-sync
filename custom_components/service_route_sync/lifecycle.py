"""Feature lifecycle orchestration without domain-specific implementations."""

from dataclasses import dataclass
from inspect import isawaitable
from collections.abc import Callable

from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed

from .backends.gateway_ssh import GatewayTransportError
from .routing.core import SyncError


@dataclass(frozen=True)
class Feature:
    """An optional capability and its explicit runtime prerequisites."""

    name: str
    enabled: bool
    create: Callable
    requires: tuple[str, ...] = ()
    initialize: str | None = None


async def prepare_features(runtime, features):
    failures = []
    for feature in features:
        if not feature.enabled:
            continue
        component = None
        try:
            if any(getattr(runtime, name) is None for name in feature.requires):
                raise ConfigEntryNotReady("Required feature is unavailable")
            component = feature.create()
            if isawaitable(component):
                component = await component
            if feature.initialize:
                await getattr(component, feature.initialize)()
            setattr(runtime, feature.name, component)
        except (
            KeyError,
            ValueError,
            OSError,
            GatewayTransportError,
            ConfigEntryNotReady,
            UpdateFailed,
            SyncError,
        ) as exc:
            if component is not None and hasattr(component, "async_shutdown"):
                await component.async_shutdown()
            runtime.setup_issues[feature.name] = type(exc).__name__
            failures.append(ConfigEntryNotReady(f"Feature {feature.name} is unavailable"))
    return failures
