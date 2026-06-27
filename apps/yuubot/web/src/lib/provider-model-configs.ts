import type { ModelConfig } from "@/types/api";

export const PROVIDER_MODEL_PRICE_SOURCE =
  "Prices checked against official OpenAI and DeepSeek pricing pages on 2026-06-27.";

const CHAT_MODEL_CAPABILITIES = {
  chat: true,
  vision: false,
  tool_calling: true,
  reasoning: true,
  embedding: false,
  structured_output: true,
} satisfies ModelConfig["capabilities"];

const OPENAI_MODEL_CAPABILITIES = {
  ...CHAT_MODEL_CAPABILITIES,
  vision: true,
} satisfies ModelConfig["capabilities"];

export const DEFAULT_PROVIDER_MODEL_CONFIGS: Record<
  string,
  Record<string, ModelConfig>
> = {
  openai: {
    "gpt-5.4": {
      pricing: {
        input_per_million: 2.5,
        cached_input_per_million: 0.25,
        output_per_million: 15,
      },
      capabilities: OPENAI_MODEL_CAPABILITIES,
    },
    "gpt-5.4-mini": {
      pricing: {
        input_per_million: 0.75,
        cached_input_per_million: 0.075,
        output_per_million: 4.5,
      },
      capabilities: OPENAI_MODEL_CAPABILITIES,
    },
    "gpt-5.4-nano": {
      pricing: {
        input_per_million: 0.2,
        cached_input_per_million: 0.02,
        output_per_million: 1.25,
      },
      capabilities: OPENAI_MODEL_CAPABILITIES,
    },
  },
  deepseek: {
    "deepseek-v4-flash": {
      pricing: {
        input_per_million: 0.14,
        cached_input_per_million: 0.0028,
        output_per_million: 0.28,
      },
      capabilities: CHAT_MODEL_CAPABILITIES,
    },
    "deepseek-v4-pro": {
      pricing: {
        input_per_million: 0.435,
        cached_input_per_million: 0.003625,
        output_per_million: 0.87,
      },
      capabilities: CHAT_MODEL_CAPABILITIES,
    },
  },
};

export function defaultModelConfigsForProvider(
  providerKey: string,
): Record<string, ModelConfig> {
  return cloneModelConfigs(DEFAULT_PROVIDER_MODEL_CONFIGS[providerKey] ?? {});
}

export function hasDefaultModelConfigs(providerKey: string): boolean {
  return Object.keys(DEFAULT_PROVIDER_MODEL_CONFIGS[providerKey] ?? {}).length > 0;
}

function cloneModelConfigs(
  configs: Record<string, ModelConfig>,
): Record<string, ModelConfig> {
  return Object.fromEntries(
    Object.entries(configs).map(([model, config]) => [
      model,
      {
        pricing: { ...config.pricing },
        capabilities: { ...config.capabilities },
      },
    ]),
  );
}
