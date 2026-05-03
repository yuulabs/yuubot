"""RFC2 agent-facing function package.

Each domain is a sub-package importable directly by characters::

    ya.PythonImport("yuubot.agent_fns.im", alias="im")
    ya.PythonImport("yuubot.agent_fns.mem", alias="mem")
    # … etc.

Most functions execute locally in the Python worker using session DB/config
paths; daemon RPC is reserved for operations that need daemon-owned runtime
state.
"""

from __future__ import annotations

from typing import TypedDict

from yuubot.agent_fns.context import (
    SessionState,
    current_session_state,
    session_state_from_mapping,
)
from yuubot.agent_fns.ops import bash
from yuubot.agent_fns.vision import describe_image, image_metadata

session_state = current_session_state


class ImageMetadata(TypedDict, total=False):
    url: str
    local_path: str
    media_id: str
    mime_type: str
    bytes: int


class ImageDescription(ImageMetadata):
    media: str
    description: str
    cached: bool


__all__ = [
    "SessionState",
    "bash",
    "current_session_state",
    "describe_image",
    "image_metadata",
    "session_state",
    "session_state_from_mapping",
]
