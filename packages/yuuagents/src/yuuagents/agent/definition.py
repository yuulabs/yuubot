from __future__ import annotations

import tomllib

from attrs import define, field

from yuuagents.core.budget import Budget
from yuuagents.types.values import LlmOptions, ToolConfig, validate_json_object


@define
class LlmConfig:
    provider: str = ""
    model: str = ""
    max_tokens: int | None = None
    stream_options: LlmOptions = field(factory=dict)

    def stream_kwargs(self) -> LlmOptions:
        kwargs: LlmOptions = dict(self.stream_options)
        kwargs.pop("model", None)
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        return kwargs


@define
class BudgetConfig:
    max_steps: int = 0
    max_tokens: int = 0
    max_usd: float = 0.0

    def to_budget(self) -> Budget:
        limits: dict[str, float] = {}
        if self.max_steps:
            limits["steps"] = float(self.max_steps)
        if self.max_tokens:
            limits["tokens"] = float(self.max_tokens)
        if self.max_usd:
            limits["usd"] = self.max_usd
        return Budget(limits=limits)


def _tools_dict(
    value: object | None,
) -> dict[str, ToolConfig]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("tools must be a mapping")
    return {
        str(k): validate_json_object(dict(v)) if isinstance(v, dict) else dict(v)
        for k, v in value.items()
    }


@define
class PromptDefinition:
    system: str = ""


@define
class AgentDefinition:
    """Full agent specification: LLM config, budget, tools, and prompt."""

    name: str = ""
    llm: LlmConfig = field(factory=LlmConfig)
    budget: BudgetConfig = field(factory=BudgetConfig)
    tools: dict[str, ToolConfig] = field(factory=dict, converter=_tools_dict)
    prompt: PromptDefinition = field(factory=PromptDefinition)

    @classmethod
    def from_file(cls, path: str) -> "AgentDefinition":
        with open(path, "rb") as f:
            data = validate_json_object(tomllib.load(f))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "AgentDefinition":
        llm_data = _json_object(data.get("llm"))
        llm = LlmConfig(
            provider=str(llm_data.pop("provider", "")),
            model=str(llm_data.pop("model", "")),
            max_tokens=(
                _int_config(max_tokens)
                if (max_tokens := llm_data.pop("max_tokens", None)) is not None
                else None
            ),
            stream_options=llm_data,
        )

        budget_data = _json_object(data.get("budget"))
        budget = BudgetConfig(
            max_steps=_int_config(budget_data.get("max_steps")),
            max_tokens=_int_config(budget_data.get("max_tokens")),
            max_usd=_float_config(budget_data.get("max_usd")),
        )

        tools = _tools_dict(data.get("tools", {}))

        raw_prompt = _json_object(data.get("prompt"))
        prompt = PromptDefinition(system=str(raw_prompt.get("system", "")))

        return cls(
            name=str(data.get("name", "")),
            llm=llm,
            budget=budget,
            tools=tools,
            prompt=prompt,
        )


def _json_object(value: object | None) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _int_config(value: object | None) -> int:
    if value is None:
        return 0
    if isinstance(value, bool | int | float | str):
        return int(value)
    raise TypeError(f"expected scalar integer config value, got {type(value).__name__}")


def _float_config(value: object | None) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool | int | float | str):
        return float(value)
    raise TypeError(f"expected scalar float config value, got {type(value).__name__}")
