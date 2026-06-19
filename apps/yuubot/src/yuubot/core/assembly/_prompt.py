"""System prompt construction helpers."""

from __future__ import annotations

from typing import Literal

from yuubot.core.bindings import AgentBinding

from ._constants import IM_MODE_SYSTEM_GUIDANCE


def _system_prompt(
    binding: AgentBinding,
    mode: Literal["im", "conversation"],
) -> str:
    fragments = [
        _runtime_guidance(binding),
        _capability_prompt(binding),
        binding.character.system_prompt.strip(),
    ]
    if mode == "im":
        fragments.append(IM_MODE_SYSTEM_GUIDANCE)
    return "\n\n".join(fragment for fragment in fragments if fragment)


def _runtime_guidance(binding: AgentBinding) -> str:
    policy = binding.capability_set.runtime_policy
    lines = [
        "Runtime:",
        "- Tool calls run as Tasks.",
        "- You are active while generating or while waiting for a foreground tool result.",
    ]
    if policy.idle_timeout_s > 0:
        lines.append(
            f"- Agent instances may expire after {policy.idle_timeout_s:g}s of idle time."
        )
    return "\n".join(lines)


def _capability_prompt(binding: AgentBinding) -> str:
    cap = binding.capability_set
    lines = [f"Capability Set: {cap.name}"]
    if cap.description:
        lines.append(cap.description)
    if binding.workspace_path:
        lines.extend(("", "Workspace:", f"- Local path: {binding.workspace_path}"))
    elif cap.workspace_path:
        lines.extend(("", "Workspace:", f"- Name: {cap.workspace_path}"))
    if cap.bootstrap_path:
        lines.extend(
            (
                "",
                "Bootstrap:",
                f"- Path: {cap.bootstrap_path}",
                "- bootstrap.md tells you what to do, what is in progress, and the workspace project map.",
                "- If you update bootstrap.md, you will see it next time an agent instance starts for this workspace.",
            )
        )
    if cap.integration_capability_ids:
        lines.extend(("", "Integration capabilities:"))
        lines.extend(f"- {capability_id}" for capability_id in cap.integration_capability_ids)
        lines.extend((
            "",
            "Note: Some tools may fail at runtime if the corresponding integration",
            "is disabled or malfunctioning. If a tool call fails, tell the user",
            "honestly what went wrong. Do not fabricate results.",
        ))
    if cap.tool_ids:
        lines.extend(("", "Tools:"))
        lines.extend(f"- {tool_id}" for tool_id in cap.tool_ids)
    if cap.prompt_fragments:
        lines.append("")
        lines.extend(fragment.strip() for fragment in cap.prompt_fragments if fragment.strip())
    return "\n".join(lines)
