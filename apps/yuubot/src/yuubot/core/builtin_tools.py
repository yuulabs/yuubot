"""Built-in capability ids backed by yuuagents tools."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinCapability:
    capability_id: str
    capability_name: str
    description: str
    namespace: str
    tool_name: str


BUILTIN_CAPABILITIES: tuple[BuiltinCapability, ...] = (
    BuiltinCapability(
        capability_id="builtin.read",
        capability_name="Read Workspace File",
        description=(
            "Read a UTF-8 text file or image from the actor workspace. "
            "Images are returned as multimodal content."
        ),
        namespace="builtin",
        tool_name="read",
    ),
    BuiltinCapability(
        capability_id="builtin.edit",
        capability_name="Edit Workspace File",
        description=(
            "Edit a UTF-8 text file in the actor workspace by replacing one "
            "exact string match."
        ),
        namespace="builtin",
        tool_name="edit",
    ),
    BuiltinCapability(
        capability_id="builtin.write",
        capability_name="Write Workspace File",
        description="Write a UTF-8 text file under the actor workspace.",
        namespace="builtin",
        tool_name="write",
    ),
    BuiltinCapability(
        capability_id="builtin.bash",
        capability_name="Run Bash Command",
        description="Run one initialized bash command under the actor workspace.",
        namespace="builtin",
        tool_name="bash",
    ),
)

BUILTIN_CAPABILITY_BY_ID = {
    capability.capability_id: capability for capability in BUILTIN_CAPABILITIES
}
