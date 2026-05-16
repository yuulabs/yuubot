"""Start yuuagents actors from yuubot core bindings."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

import msgspec

from yuuagents import (
    Actor as YuuAgentsActor,
    AgentDefinition,
    LlmConfig,
    PromptDefinition,
    Stage,
    StageConfig,
)
from yuuagents.agent import LlmClient

from yuubot.bootstrap.config import YuuAgentsConfig
from yuubot.core.bindings import ActorBinding
from yuubot.core.costing import PricingAwareLlmClient
from yuubot.core.facade import ActorFacadeBinding
from yuubot.core.observability import TraceObserver
from yuubot.core.validation import (
    ConfigurationError,
    validate_capability_config,
    validate_prompt_provider_config,
    validate_provider_options,
    validate_stream_options,
)
from yuubot.resources.records import (
    CapabilityConfig,
    PromptProviderConfig,
)

PYTHON_PROVIDER_KEY = "ipykernel"
YEXT_IMPORTS = [{"module": "yext"}, {"module": "yext.echo"}]
YEXT_EXPAND_FUNCTIONS = ["yext.*", "yext.echo.*"]


def start_yuuagents_actor(
    binding: ActorBinding,
    *,
    yuuagents_config: YuuAgentsConfig,
    facade: ActorFacadeBinding | None = None,
    llm_client: LlmClient | None = None,
    observer: TraceObserver | None = None,
) -> YuuAgentsActor:
    _check_pricing_for_budget(binding)
    stage = Stage.from_config(
        StageConfig(
            strict=yuuagents_config.strict,
            providers=_stage_provider_config(
                yuuagents_config,
                binding=binding,
                facade=facade,
            ),
            llm=llm_client or _stage_llm_config(binding),
        )
    )
    stage.llm_provider = PricingAwareLlmClient(
        inner=stage.llm_provider,
        pricing=binding.llm.backend.pricing,
        configured_model=binding.llm.model,
    )
    actor = YuuAgentsActor(stage, [build_agent_definition(binding, facade=facade)])
    if observer is not None:
        stage.eventbus.subscribe(observer)
    return actor


def build_agent_definition(
    binding: ActorBinding,
    *,
    facade: ActorFacadeBinding | None = None,
) -> AgentDefinition:
    actor = binding.actor
    return AgentDefinition(
        name=actor.name,
        llm=LlmConfig(
            model=binding.llm.model,
            max_tokens=actor.llm_options.max_tokens,
            stream_options=msgspec.to_builtins(actor.llm_options.stream_options),
        ),
        budget=actor.budget.to_budget_config(),
        capabilities=_agent_capability_configs(actor.agent_capabilities, facade),
        prompts=PromptDefinition(
            system=binding.character.system_prompt,
            providers=_agent_prompt_provider_configs(
                (
                    *binding.character.default_prompt_providers,
                    *actor.agent_prompt_providers,
                ),
                facade,
            ),
        ),
    )


def _stage_llm_config(binding: ActorBinding) -> dict[str, object]:
    backend = binding.llm.backend
    provider_options = validate_provider_options(
        msgspec.to_builtins(backend.provider_options),
        context=f"llm_backend[{backend.name}].provider_options",
    )
    stream_options = validate_stream_options(
        msgspec.to_builtins(backend.default_stream_options),
        context=f"llm_backend[{backend.name}].default_stream_options",
    )
    return {
        "provider": backend.yuuagents_provider,
        "model": binding.llm.model,
        "provider_options": provider_options,
        "stream_options": stream_options,
    }


def _stage_provider_config(
    yuuagents_config: YuuAgentsConfig,
    *,
    binding: ActorBinding,
    facade: ActorFacadeBinding | None,
) -> dict[str, Any]:
    providers = {
        key: _copy_provider_config(value)
        for key, value in yuuagents_config.providers.items()
    }
    if facade is not None:
        providers[PYTHON_PROVIDER_KEY] = _python_provider_config(
            providers.get(PYTHON_PROVIDER_KEY),
            binding=binding,
            facade=facade,
        )
    return providers


def _copy_provider_config(config: object) -> Any:
    if isinstance(config, Mapping):
        return dict(config)
    return config


def _capability_configs(
    configs: Iterable[CapabilityConfig],
) -> dict[str, dict[str, Any]]:
    return {
        item.provider_key: validate_capability_config(
            item.provider_key, dict(item.config)
        )
        for item in configs
    }


def _agent_capability_configs(
    configs: Iterable[CapabilityConfig],
    facade: ActorFacadeBinding | None,
) -> dict[str, dict[str, Any]]:
    result = _capability_configs(configs)
    if facade is not None:
        result[PYTHON_PROVIDER_KEY] = _python_capability_config(
            result.get(PYTHON_PROVIDER_KEY),
            facade,
        )
    return result


def _prompt_provider_configs(
    configs: Iterable[PromptProviderConfig],
) -> dict[str, dict[str, Any]]:
    return {
        item.provider_key: validate_prompt_provider_config(
            item.provider_key, dict(item.config)
        )
        for item in configs
    }


def _agent_prompt_provider_configs(
    configs: Iterable[PromptProviderConfig],
    facade: ActorFacadeBinding | None,
) -> dict[str, dict[str, Any]]:
    result = _prompt_provider_configs(configs)
    if facade is not None:
        result.setdefault(PYTHON_PROVIDER_KEY, {"level": "summary"})
    return result


def _python_provider_config(
    existing: object,
    *,
    binding: ActorBinding,
    facade: ActorFacadeBinding,
) -> dict[str, Any]:
    provider = dict(existing) if isinstance(existing, Mapping) else {}
    raw_config = provider.get("config")
    kernel_config = dict(raw_config) if isinstance(raw_config, Mapping) else {}
    kernel_config["cwd"] = str(binding.require_workspace_path())
    kernel_config["sys_path"] = facade.sys_path
    kernel_config["startup_code"] = _merged_startup_code(
        str(kernel_config.get("startup_code", "")),
        facade.startup_code,
    )
    provider["config"] = kernel_config
    return provider


def _python_capability_config(
    existing: dict[str, Any] | None,
    facade: ActorFacadeBinding,
) -> dict[str, Any]:
    config = dict(existing or {})
    config["imports"] = _merged_imports(config.get("imports"), YEXT_IMPORTS)
    config["expand_functions"] = _merged_str_sequence(
        config.get("expand_functions"),
        YEXT_EXPAND_FUNCTIONS,
    )
    state_raw = config.get("state")
    state = (
        dict(cast(Mapping[str, object], state_raw))
        if isinstance(state_raw, Mapping)
        else {}
    )
    state.setdefault("actor_id", facade.actor_id)
    state.setdefault("agent_id", facade.agent_id)
    config["state"] = state
    return config


def _merged_imports(
    existing: object,
    required_imports: list[dict[str, str]],
) -> list[object]:
    imports = list(existing) if isinstance(existing, Sequence) and not isinstance(existing, str) else []
    existing_modules = {_import_module(item) for item in imports}
    for required_import in required_imports:
        if required_import["module"] not in existing_modules:
            imports.append(dict(required_import))
    return imports


def _import_module(item: object) -> str:
    if isinstance(item, Mapping):
        module = cast(Mapping[object, object], item).get("module")
        return module if isinstance(module, str) else ""
    return item if isinstance(item, str) else ""


def _merged_str_sequence(existing: object, required: list[str]) -> list[str]:
    values = [
        item
        for item in (existing if isinstance(existing, Sequence) and not isinstance(existing, str) else ())
        if isinstance(item, str)
    ]
    for item in required:
        if item not in values:
            values.append(item)
    return values


def _merged_startup_code(existing: str, required: str) -> str:
    if not existing:
        return required
    if required in existing:
        return existing
    return f"{existing}\n{required}"


def _check_pricing_for_budget(binding: ActorBinding) -> None:
    if not _requires_pricing(binding):
        return
    model = binding.llm.model
    for entry in binding.llm.backend.pricing.entries:
        if entry.model == model:
            return
    raise ConfigurationError(
        f"actor {binding.actor.name!r}: USD budget requires pricing for "
        f"model {model!r} in backend {binding.llm.backend.name!r}"
    )


def _requires_pricing(binding: ActorBinding) -> bool:
    backend_budget = binding.llm.backend.budget
    return (
        binding.actor.budget.max_usd > 0
        or _positive_budget(backend_budget.daily_usd)
        or _positive_budget(backend_budget.monthly_usd)
    )


def _positive_budget(value: float | None) -> bool:
    return value is not None and value > 0
