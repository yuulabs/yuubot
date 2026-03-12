"""Shared error hierarchy for yuubot."""

from __future__ import annotations


class YuubotError(Exception):
    """Base error for yuubot."""


class ConfigurationError(YuubotError):
    """Invalid or missing configuration."""


class CapabilityError(YuubotError):
    """Capability execution failed."""


class MessageSendError(YuubotError):
    """Failed to send a message."""
