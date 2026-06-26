/**
 * Built-in preset Actor definitions shared by the Providers onboarding flow
 * and the Actors page "update preset Actors" action.
 *
 * References the stable seeded Character / CapabilitySet ids (seeded by the
 * daemon's `builtin_presets.py`). The frontend never mints replacement
 * Character / CapabilitySet records — if a referenced id is absent the Actor
 * create call surfaces the backend's mutation error to the caller.
 */

export interface PresetActor {
  actorName: string;
  characterId: string;
  capabilitySetId: string;
}

export const PRESET_ACTORS: readonly PresetActor[] = [
  {
    actorName: "general",
    characterId: "builtin-character-general",
    capabilitySetId: "builtin-capability-general",
  },
  {
    actorName: "shiori",
    characterId: "builtin-character-shiori",
    capabilitySetId: "builtin-capability-shiori",
  },
] as const;

export interface LLMBackendLike {
  id: string;
  default_model?: string | null;
}

/**
 * Build the Actor create payload that binds a preset Actor to a backend.
 * `max_usd` is non-zero so the daemon's pricing guard is actually exercised
 * (budget=0 silently disables the guard); matches the onboarding contract.
 */
export function presetActorCreatePayload(
  preset: PresetActor,
  backend: LLMBackendLike,
): Record<string, unknown> {
  return {
    name: preset.actorName,
    type: "simple_loop",
    enabled: true,
    default_model: backend.default_model ?? "",
    default_character_id: preset.characterId,
    capability_set_id: preset.capabilitySetId,
    default_llm_backend_id: backend.id,
    default_budget: { max_steps: 6, max_tokens: 8192, max_usd: 2.0 },
  };
}
