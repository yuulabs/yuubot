"""Capability contract types — machine-readable action specs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import msgspec
import yaml


class ActionContract(msgspec.Struct, frozen=True):
    name: str
    summary: str
    usage: str
    payload_rule: str
    return_shape: str  # "text", "json", "none"


class CapabilityContract(msgspec.Struct, frozen=True):
    name: str
    summary: str
    actions: list[ActionContract]


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
        )
        for a in raw.get("actions", [])
    ]
    return CapabilityContract(
        name=raw["name"],
        summary=raw.get("summary", ""),
        actions=actions,
    )


_CONTRACT_DIR = Path(__file__).parent / "contracts"


def load_all_contracts() -> dict[str, CapabilityContract]:
    """Load all YAML contracts from the contracts/ directory."""
    result: dict[str, CapabilityContract] = {}
    if not _CONTRACT_DIR.is_dir():
        return result
    for p in sorted(_CONTRACT_DIR.glob("*.yaml")):
        contract = load_contract(p)
        result[contract.name] = contract
    return result
