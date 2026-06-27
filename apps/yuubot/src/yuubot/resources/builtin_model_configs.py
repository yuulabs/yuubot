"""Built-in model pricing for first-party provider presets."""

from __future__ import annotations

from yuubot.resources.records import ModelCapabilities, ModelConfig, Pricing


PRICE_SOURCE_NOTE = (
    "Prices checked against official OpenAI and DeepSeek pricing pages on 2026-06-27."
)

_CHAT_MODEL_CAPABILITIES = ModelCapabilities(
    chat=True,
    vision=False,
    tool_calling=True,
    reasoning=True,
    embedding=False,
    structured_output=True,
)

_OPENAI_MODEL_CAPABILITIES = ModelCapabilities(
    chat=True,
    vision=True,
    tool_calling=True,
    reasoning=True,
    embedding=False,
    structured_output=True,
)


DEFAULT_PROVIDER_MODEL_CONFIGS: dict[str, dict[str, ModelConfig]] = {
    "openai": {
        "gpt-5.4": ModelConfig(
            pricing=Pricing(
                input_per_million=2.5,
                cached_input_per_million=0.25,
                output_per_million=15.0,
            ),
            capabilities=_OPENAI_MODEL_CAPABILITIES,
        ),
        "gpt-5.4-mini": ModelConfig(
            pricing=Pricing(
                input_per_million=0.75,
                cached_input_per_million=0.075,
                output_per_million=4.5,
            ),
            capabilities=_OPENAI_MODEL_CAPABILITIES,
        ),
        "gpt-5.4-nano": ModelConfig(
            pricing=Pricing(
                input_per_million=0.2,
                cached_input_per_million=0.02,
                output_per_million=1.25,
            ),
            capabilities=_OPENAI_MODEL_CAPABILITIES,
        ),
    },
    "deepseek": {
        "deepseek-v4-flash": ModelConfig(
            pricing=Pricing(
                input_per_million=0.14,
                cached_input_per_million=0.0028,
                output_per_million=0.28,
            ),
            capabilities=_CHAT_MODEL_CAPABILITIES,
        ),
        "deepseek-v4-pro": ModelConfig(
            pricing=Pricing(
                input_per_million=0.435,
                cached_input_per_million=0.003625,
                output_per_million=0.87,
            ),
            capabilities=_CHAT_MODEL_CAPABILITIES,
        ),
    },
}


def default_model_configs_for_provider(
    provider_identity: str,
) -> dict[str, ModelConfig]:
    """Return built-in model configs for a provider preset."""
    return {
        model: _clone_model_config(config)
        for model, config in DEFAULT_PROVIDER_MODEL_CONFIGS.get(
            provider_identity, {}
        ).items()
    }


def _clone_model_config(config: ModelConfig) -> ModelConfig:
    return ModelConfig(
        pricing=Pricing(
            input_per_million=config.pricing.input_per_million,
            cached_input_per_million=config.pricing.cached_input_per_million,
            output_per_million=config.pricing.output_per_million,
        ),
        capabilities=ModelCapabilities(
            chat=config.capabilities.chat,
            vision=config.capabilities.vision,
            tool_calling=config.capabilities.tool_calling,
            reasoning=config.capabilities.reasoning,
            embedding=config.capabilities.embedding,
            structured_output=config.capabilities.structured_output,
        ),
    )
