"""Integration contracts, registry, lifecycle, and invocation."""

from yuubot.core.integrations.context import InvocationContext, bind_invocation_context
from yuubot.core.integrations.contracts import (
    IntegrationFactory,
    IntegrationInstance,
    IntegrationKindInfo,
    IntegrationStorage,
    LocalIntegrationStorage,
)
from yuubot.core.integrations.core import CapabilityInstanceInfo, IntegrationCore
from yuubot.core.integrations.registry import (
    IntegrationFactoryRegistry,
    default_integration_factories,
)

__all__ = [
    "CapabilityInstanceInfo",
    "IntegrationCore",
    "IntegrationFactory",
    "IntegrationFactoryRegistry",
    "IntegrationInstance",
    "IntegrationKindInfo",
    "IntegrationStorage",
    "InvocationContext",
    "LocalIntegrationStorage",
    "bind_invocation_context",
    "default_integration_factories",
]
