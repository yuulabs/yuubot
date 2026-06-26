"""Provider/LLM backend admin handlers.

Handler factories for listing models, validating provider
connectivity, and provider utility functions for client
creation and introspection.
"""

from __future__ import annotations

import httpx
import yuullm
from starlette.requests import Request
from starlette.responses import JSONResponse

from yuubot.core.assembly._llm_session import provider_key_for_backend
from yuubot.resources.records import LLMBackendRecord
from yuubot.resources.root import Resources
from yuubot.resources.store.models import LLMBackendORM

from ._helpers import _error, _optional_json_body, _string_payload_value
from ._types import CreateProviderModelClientFn


# -- Provider helpers --


def _create_provider_model_client(
    backend: LLMBackendRecord,
    *,
    api_key: str = "",
    base_url: str = "",
) -> yuullm.Provider:
    provider_key = _provider_key(backend)
    preset = yuullm.resolve_provider(backend.provider_identity)
    provider_api_key = api_key or backend.provider_options.api_key.reveal()
    provider_base_url = base_url or backend.provider_options.base_url or preset.default_base_url

    if provider_key == "anthropic":
        return yuullm.providers.AnthropicProvider(
            api_key=provider_api_key or None,
            base_url=provider_base_url or None,
        )
    if provider_key == "openrouter":
        return yuullm.providers.OpenRouterProvider(api_key=provider_api_key)
    return yuullm.providers.OpenAIProvider(
        api_key=provider_api_key or None,
        base_url=provider_base_url or None,
        provider_name=provider_key,
    )


def _provider_key(backend: LLMBackendRecord) -> str:
    return provider_key_for_backend(backend)


def _provider_model_payload(model: yuullm.ProviderModel) -> dict[str, object]:
    payload: dict[str, object] = {"id": model.id}
    if model.display_name is not None:
        payload["displayName"] = model.display_name
    if model.supports_vision is not None:
        payload["supportsVision"] = model.supports_vision
    return payload


def _provider_capabilities_payload(backend: LLMBackendRecord) -> dict[str, bool]:
    selected_model = _selected_model(backend)
    config = backend.model_configs.get(selected_model) if selected_model else None
    if config is None:
        return {
            "chat": False,
            "vision": False,
            "tool_calling": False,
            "reasoning": False,
            "embedding": False,
            "structured_output": False,
        }
    capabilities = config.capabilities
    return {
        "chat": capabilities.chat,
        "vision": capabilities.vision,
        "tool_calling": capabilities.tool_calling,
        "reasoning": capabilities.reasoning,
        "embedding": capabilities.embedding,
        "structured_output": capabilities.structured_output,
    }


def _selected_model(backend: LLMBackendRecord) -> str:
    if backend.recommended_model:
        return backend.recommended_model
    return next(iter(backend.model_configs), "")


# -- Handler factories --


def make_provider_models_handler(
    *,
    resources: Resources,
    _create_provider_model_client_fn: CreateProviderModelClientFn | None = None,
):
    _create_client = (
        _create_provider_model_client_fn
        if _create_provider_model_client_fn is not None
        else _create_provider_model_client
    )

    async def provider_models(request: Request) -> JSONResponse:
        backend_id = request.path_params["id"]
        payload = await _optional_json_body(request)
        if isinstance(payload, JSONResponse):
            return payload

        backend = await resources.repository.get(LLMBackendORM, backend_id)
        if backend is None:
            return _error("not_found", "llm backend not found", 404)

        try:
            client = _create_client(
                backend,
                api_key=_string_payload_value(payload, "api_key"),
                base_url=_string_payload_value(payload, "base_url"),
            )
            models = await client.list_models()
        except ValueError as exc:
            return _error("validation_error", str(exc), 400)
        except (OSError, httpx.HTTPError) as exc:
            return _error("provider_model_fetch_failed", str(exc), 502)

        return JSONResponse(
            {
                "status": "ok",
                "data": [_provider_model_payload(model) for model in models],
            }
        )

    return provider_models


def make_validate_provider_handler(
    *,
    resources: Resources,
    _create_provider_model_client_fn: CreateProviderModelClientFn | None = None,
):
    _create_client = (
        _create_provider_model_client_fn
        if _create_provider_model_client_fn is not None
        else _create_provider_model_client
    )

    async def validate_provider(request: Request) -> JSONResponse:
        backend_id = request.path_params["id"]
        payload = await _optional_json_body(request)
        if isinstance(payload, JSONResponse):
            return payload

        backend = await resources.repository.get(LLMBackendORM, backend_id)
        if backend is None:
            return _error("not_found", "llm backend not found", 404)

        try:
            client = _create_client(
                backend,
                api_key=_string_payload_value(payload, "api_key"),
                base_url=_string_payload_value(payload, "base_url"),
            )
            models = await client.list_models()
        except ValueError as exc:
            return _error("validation_error", str(exc), 400)
        except (OSError, httpx.HTTPError) as exc:
            return JSONResponse(
                {
                    "status": "ok",
                    "data": {
                        "valid": False,
                        "detail": str(exc),
                        "recommended_model_valid": False,
                        "models": [],
                        "capabilities": _provider_capabilities_payload(backend),
                    },
                }
            )

        model_ids = [model.id for model in models]
        selected_model = _selected_model(backend)
        return JSONResponse(
            {
                "status": "ok",
                "data": {
                    "valid": True,
                    "detail": "",
                    "recommended_model_valid": (
                        not selected_model or selected_model in set(model_ids)
                    ),
                    "models": [_provider_model_payload(model) for model in models],
                    "capabilities": _provider_capabilities_payload(backend),
                },
            }
        )

    return validate_provider
