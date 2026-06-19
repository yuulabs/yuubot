"""Tests for AgentDefinition parsing (from_dict, struct equivalence, from TOML)."""

from __future__ import annotations

import tempfile
import os


from yuuagents.agent.definition import AgentDefinition, LlmConfig, PromptDefinition
from yuuagents.types.values import validate_json_object


# ---------------------------------------------------------------------------
# AgentDefinition.from_dict
# ---------------------------------------------------------------------------


def test_agent_definition_from_dict_basic() -> None:
    d = validate_json_object(
        {
            "llm": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "max_tokens": 8096,
            },
            "budget": {"max_steps": 80, "max_tokens": 200_000},
            "tools": {
                "bash": {},
                "fileop": {},
            },
            "prompt": {
                "system": "You are Shiori.",
            },
        }
    )
    defn = AgentDefinition.from_dict(d)

    assert defn.llm.provider == "anthropic"
    assert defn.llm.model == "claude-sonnet-4-6"
    assert defn.llm.max_tokens == 8096
    assert defn.budget.max_steps == 80
    assert defn.budget.max_tokens == 200_000
    assert "bash" in defn.tools
    assert "fileop" in defn.tools
    assert defn.prompt.system == "You are Shiori."


def test_agent_definition_from_dict_struct_equivalence() -> None:
    d = validate_json_object(
        {
            "llm": {"provider": "openai", "model": "gpt-4o"},
            "prompt": {"system": "Hello"},
        }
    )
    defn_from_dict = AgentDefinition.from_dict(d)
    defn_direct = AgentDefinition(
        llm=LlmConfig(provider="openai", model="gpt-4o"),
        prompt=PromptDefinition(system="Hello"),
    )
    assert defn_from_dict.llm.provider == defn_direct.llm.provider
    assert defn_from_dict.llm.model == defn_direct.llm.model
    assert defn_from_dict.prompt.system == defn_direct.prompt.system


# ---------------------------------------------------------------------------
# AgentDefinition.from_file (TOML)
# ---------------------------------------------------------------------------


def test_agent_definition_from_toml_file() -> None:
    toml_content = b"""
[llm]
provider = "anthropic"
model = "claude-sonnet-4-6"
max_tokens = 8096

[budget]
max_steps = 80
max_tokens = 200000

[tools.bash]

[prompt]
system = "You are Shiori, a helpful assistant."
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        f.write(toml_content)
        tmppath = f.name

    try:
        defn = AgentDefinition.from_file(tmppath)
        assert defn.llm.model == "claude-sonnet-4-6"
        assert defn.budget.max_steps == 80
        assert defn.prompt.system == "You are Shiori, a helpful assistant."
    finally:
        os.unlink(tmppath)
