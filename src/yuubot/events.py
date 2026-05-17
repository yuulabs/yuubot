"""Backward-compat shim — implementation moved to yuubot.core.events."""

from yuubot.core.events import (  # noqa: F401
    Event as Event,
    EventBus as EventBus,
    EventSubscription as EventSubscription,
    QueuedEvent as QueuedEvent,
)
