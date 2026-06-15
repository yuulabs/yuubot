"""Backward-compatible re-export shim.

All implementation has moved to :mod:`yuubot.runtime.plugin`.
This module exists so that existing ``from yuubot.runtime.plugin_manager import ...``
statements continue to work unchanged.
"""

from __future__ import annotations

from yuubot.runtime.plugin import *  # noqa: F403
