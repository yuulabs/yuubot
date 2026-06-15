"""LLM session factory construction for yuuagents actors."""

from __future__ import annotations

from urllib.parse import urlparse

import yuullm
from yuuagents import ProviderPoolSessionFactory

from yuubot.core.bindings import ActorBinding
from yuubot.resources.records import LLMBackendRecord

from ._constants import _resolve_yuuagents_provider


def llm_session_factory_for_binding(
    binding: ActorBinding,
) -> ProviderPoolSessionFactory:
    backend = binding.llm.backend
    provider_key = provider_key_for_backend(backend)
    provider_spec = yuullm.ProviderSpec(
        name=provider_key,
        api_type=_api_type_for_provider(provider_key),
        api_key=backend.provider_options.api_key,
        base_url=_provider_base_url(provider_key, backend),
        extra={
            "timeout": backend.provider_options.timeout,
            "max_retries": backend.provider_options.max_retries,
        },
    )
    pool = yuullm.ProviderPool({provider_key: provider_spec})
    return ProviderPoolSessionFactory(pool=pool)


def provider_key_for_backend(backend: LLMBackendRecord) -> str:
    provider_name = backend.provider_options.provider_name.strip()
    if provider_name:
        return provider_name
    base_url_key = _provider_key_from_base_url(backend.provider_options.base_url)
    if base_url_key:
        return base_url_key
    return _resolve_yuuagents_provider(backend.yuuagents_provider)


def _api_type_for_provider(provider_key: str) -> str:
    if provider_key == "anthropic":
        return "anthropic-messages"
    return "openai-chat-completion"


def _provider_base_url(provider_key: str, backend: LLMBackendRecord) -> str:
    if backend.provider_options.base_url:
        return backend.provider_options.base_url
    if provider_key == "openrouter":
        return "https://openrouter.ai/api/v1"
    return ""


def _provider_key_from_base_url(base_url: str) -> str:
    hostname = urlparse(base_url).hostname or ""
    if hostname == "api.deepseek.com":
        return "deepseek"
    if hostname == "api.groq.com":
        return "groq"
    if hostname == "generativelanguage.googleapis.com":
        return "google"
    if hostname == "api.x.ai":
        return "xai"
    return ""
