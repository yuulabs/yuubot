/**
 * Built-in preset Actor definitions shared by the Providers onboarding flow
 * and the Actors page "update preset Actors" action.
 */

export interface PresetActor {
  actorName: string;
  personaPrompt: string;
  capabilitySetId: string;
}

export const PRESET_ACTORS: readonly PresetActor[] = [
  {
    actorName: "general",
    personaPrompt: "You are a helpful assistant.",
    capabilitySetId: "builtin-capability-general",
  },
  {
    actorName: "shiori",
    personaPrompt:
      "你是汐织，一个可靠、温和、直接的协作助手。\n\nScenario Communication: 主动澄清不确定信息，给出可执行建议，保持简洁。",
    capabilitySetId: "builtin-capability-shiori",
  },
] as const;

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
    name: preset.actorName,
    type: "simple_loop",
    enabled: true,
    persona_prompt: preset.personaPrompt,
    model: firstConfiguredModel(backend),
    capability_set_id: preset.capabilitySetId,
    llm_backend_id: backend.id,
    per_run_budget: {},
  };
}

function firstConfiguredModel(backend: LLMBackendLike): string {
  return Object.keys(backend.model_configs).sort()[0] ?? "";
}
