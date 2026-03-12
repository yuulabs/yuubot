"""Addon framework — backwards compatibility bridge.

This module re-exports from yuubot.capabilities. All new code should
import from yuubot.capabilities directly. This bridge will be removed
in a future version.
"""

from __future__ import annotations

# Re-export everything from capabilities
from yuubot.capabilities import (
    ContentBlock,
    uri_to_path,
    path_to_uri,
    text_block,
    image_block,
    CapabilityContext as AddonContext,
    get_context,
    capability as addon,
    get_capability as get_addon,
    registered_capabilities as registered_addons,
    execute,
    load_capability_doc as load_addon_doc,
    capability_summary as addon_summary,
    _parse_command,
    _parse_args,
    _coerce,
)

# Re-export the registry internals for any code that accesses them
from yuubot.capabilities import _REGISTRY, _INSTANCES

# Import addon modules to ensure they are registered via capabilities
from yuubot.addons import im      # noqa: E402, F401
from yuubot.addons import mem     # noqa: E402, F401
from yuubot.addons import web     # noqa: E402, F401
from yuubot.addons import img     # noqa: E402, F401
from yuubot.addons import schedule as _schedule  # noqa: E402, F401
from yuubot.addons import hhsh    # noqa: E402, F401
from yuubot.addons import vision  # noqa: E402, F401
