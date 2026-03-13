"""Capability contract types and prompt-facing rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import msgspec
import yaml


class ActionContract(msgspec.Struct, frozen=True):
    name: str
    summary: str
    usage: str
    payload_rule: str
    return_shape: str  # "text", "json", "none"
    notes: str = ""


class CapabilityContract(msgspec.Struct, frozen=True):
    name: str
    summary: str
    actions: list[ActionContract]
    usage_guidelines: str = ""


class ActionFilter(msgspec.Struct, frozen=True):
    """How an agent sees actions for a capability."""

    mode: str = "all"  # "all" | "include" | "exclude"
    actions: frozenset[str] = frozenset()


def load_contract(path: Path) -> CapabilityContract:
    """Load a YAML contract file into a CapabilityContract."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    actions = [
        ActionContract(
            name=a["name"],
            summary=a["summary"],
            usage=a["usage"],
            payload_rule=a.get("payload_rule", "none"),
            return_shape=a.get("return_shape", "text"),
            notes=a.get("notes", ""),
        )
        for a in raw.get("actions", [])
    ]
    return CapabilityContract(
        name=raw["name"],
        summary=raw.get("summary", ""),
        actions=actions,
        usage_guidelines=raw.get("usage_guidelines", ""),
    )


_CAPABILITIES_DIR: Final[Path] = Path(__file__).parent
_CACHE_KEY: tuple[tuple[str, int], ...] | None = None
_CACHE_VALUE: dict[str, CapabilityContract] = {}


def _iter_contract_paths() -> list[Path]:
    return sorted(_CAPABILITIES_DIR.glob("*/contract.yaml"))


def _snapshot(paths: list[Path]) -> tuple[tuple[str, int], ...]:
    return tuple((str(path), path.stat().st_mtime_ns) for path in paths)


def load_all_contracts() -> dict[str, CapabilityContract]:
    """Load capability contracts once and refresh only when files changed."""
    global _CACHE_KEY, _CACHE_VALUE

    paths = _iter_contract_paths()
    key = _snapshot(paths)
    if _CACHE_KEY == key:
        return _CACHE_VALUE

    result: dict[str, CapabilityContract] = {}
    for path in paths:
        contract = load_contract(path)
        result[contract.name] = contract

    _CACHE_KEY = key
    _CACHE_VALUE = result
    return result


def filter_contract_actions(
    contract: CapabilityContract,
    action_filter: ActionFilter | None = None,
) -> CapabilityContract:
    """Return the agent-visible contract view."""
    if action_filter is None or action_filter.mode == "all":
        return contract

    if action_filter.mode == "include":
        actions = [a for a in contract.actions if a.name in action_filter.actions]
    elif action_filter.mode == "exclude":
        actions = [a for a in contract.actions if a.name not in action_filter.actions]
    else:
        raise ValueError(f"unknown action filter mode: {action_filter.mode!r}")

    return CapabilityContract(
        name=contract.name,
        summary=contract.summary,
        actions=actions,
        usage_guidelines=contract.usage_guidelines,
    )


def render_contract_doc(contract: CapabilityContract) -> str:
    """Render a capability contract into stable markdown for prompt/doc use."""
    lines = [
        f"# {contract.name} Capability",
        "",
        contract.summary,
    ]
    if contract.usage_guidelines:
        lines.extend(["", "## 使用原则", "", contract.usage_guidelines.strip()])

    lines.extend(["", "## 可用命令"])
    for action in contract.actions:
        lines.extend([
            "",
            f"### {action.name}",
            "",
            action.summary,
            "",
            "```text",
            action.usage.strip(),
            "```",
            f"- payload: {action.payload_rule}",
            f"- return: {action.return_shape}",
        ])
        if action.notes:
            lines.append(f"- notes: {action.notes}")
    return "\n".join(lines).strip()
