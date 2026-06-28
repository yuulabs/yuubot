import type { PresetActor } from "@/types/api";

export interface LLMBackendLike {
  id: string;
  model_configs: Record<string, unknown>;
}

/**
 * Build the Actor create payload that binds a preset Actor to a backend.
 * Preset Actors start without per-run limits; users can add limits later from
 * the Actor editor.
 */
export function presetActorCreatePayload(
  preset: PresetActor,
  backend: LLMBackendLike,
): Record<string, unknown> {
  return {
    name: preset.actor_name,
    type: "simple_loop",
    enabled: true,
    persona_prompt: preset.persona_prompt,
    model: firstConfiguredModel(backend),
    capability_set_id: preset.capability_set_id,
    llm_backend_id: backend.id,
    per_run_budget: {},
  };
}

function firstConfiguredModel(backend: LLMBackendLike): string {
  return Object.keys(backend.model_configs).sort()[0] ?? "";
}
