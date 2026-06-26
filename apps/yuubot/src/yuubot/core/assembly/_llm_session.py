"""LLM session factory construction for yuuagents actors."""

from __future__ import annotations

import yuullm
from yuuagents import ProviderPoolSessionFactory

from yuubot.core.bindings import AgentBinding
from yuubot.resources.records import LLMBackendRecord


def llm_session_factory_for_binding(
    binding: AgentBinding,
) -> ProviderPoolSessionFactory:
    backend = binding.llm.backend
    preset = yuullm.resolve_provider(backend.provider_identity)
    provider_key = preset.identity
    provider_spec = yuullm.ProviderSpec(
        name=provider_key,
        api_type=_pool_api_type(preset.api_type),
        api_key=backend.provider_options.api_key.reveal(),
        base_url=backend.provider_options.base_url or preset.default_base_url,
        extra={
            "timeout": backend.provider_options.timeout,
            "max_retries": backend.provider_options.max_retries,
        },
    )
    pool = yuullm.ProviderPool({provider_key: provider_spec})
    return ProviderPoolSessionFactory(pool=pool)


def provider_key_for_backend(backend: LLMBackendRecord) -> str:
    return yuullm.resolve_provider(backend.provider_identity).identity


def _pool_api_type(api_type: str) -> str:
    if api_type == "openai-compatible":
        return "openai-chat-completion"
    return api_type
