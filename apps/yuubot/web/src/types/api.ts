/** TypeScript types mirroring the yuubot backend API response shapes.
 *
 * Derived from:
 * - src/yuubot/resources/records.py (msgspec records)
 * - src/yuubot/resources/store/models.py (Tortoise ORM with references)
 * - src/yuubot/runtime/admin/app.py (admin API routes)
 * - src/yuubot/runtime/daemon/commands.py (daemon response envelope)
 *
 * Daemon responses are wrapped as { status: "ok", data: ... } and the admin
 * proxies them directly to the frontend.
 */

// ---------------------------------------------------------------------------
// Response envelopes
// ---------------------------------------------------------------------------

export interface ListResponse<T> {
  status: string;
  data: T[];
}

export interface SingleResponse<T> {
  status: string;
  data: T;
  actions?: string[];
}

export interface DeleteResponse {
  status: string;
  actions?: string[];
  warnings?: string[];
}

export interface ErrorResponse {
  status: "error";
  code?: string;
  detail?: string;
  reason?: string;
  hint?: string;
}

// ---------------------------------------------------------------------------
// Health & meta
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  admin: string;
  daemon: string;
  ingress_rules: number;
  integrations: number;
  plugins: number;
}

export interface IntegrationKind {
  name: string;
  description: string;
  config_schema?: Record<string, unknown>;
  source_path_convention?: string;
  capabilities: Array<{
    id: string;
    name: string;
    description: string;
    namespace: string;
  }>;
}

export interface PresetActor {
  actor_name: string;
  persona_prompt: string;
  capability_set_id: string;
}

// ---------------------------------------------------------------------------
// Resource type literals (matches daemon URL slugs)
// ---------------------------------------------------------------------------

export type ResourceType =
  | "llm-backends"
  | "capability-sets"
  | "actors"
  | "ingress-rules"
  | "integrations";

// ---------------------------------------------------------------------------
// Resource records (mirror msgspec struct shapes returned by the API)
// ---------------------------------------------------------------------------

/** Base fields shared by resource records. */
export interface Resource {
  id: string;
  enabled?: boolean;
  version?: number;
  created_at?: string;
  updated_at?: string;
}

/** Mirrors backend `ModelCapabilities` struct. */
export interface ModelCapabilities {
  chat?: boolean;
  vision?: boolean;
  tool_calling?: boolean;
  reasoning?: boolean;
  embedding?: boolean;
  structured_output?: boolean;
}

/** Mirrors backend `Pricing` struct. */
export interface Pricing {
  input_per_million?: number;
  cached_input_per_million?: number;
  output_per_million?: number;
}

/** Mirrors backend `ModelConfig` struct. */
export interface ModelConfig {
  pricing?: Pricing;
  capabilities?: ModelCapabilities;
}

/** Mirrors backend `BudgetPolicy`. */
export interface BudgetPolicy {
  daily_usd?: number | null;
  monthly_usd?: number | null;
}

/** Mirrors backend `LLMProviderOptions`. */
export interface LLMProviderOptions {
  base_url?: string;
  api_key?: string;
  timeout?: number;
  max_retries?: number;
}

/** Mirrors backend `GenerationParams`. */
export interface GenerationParams {
  max_tokens?: number | null;
  temperature?: number | null;
  top_p?: number | null;
  stop?: string[] | null;
}

export interface LLMBackendResource extends Resource {
  name: string;
  provider_identity: string;
  model_configs: Record<string, ModelConfig>;
  budget: BudgetPolicy;
  provider_options?: LLMProviderOptions;
  default_generation_params?: GenerationParams;
}

/** Mirrors backend `ToolSelection` — a user-configured tool entry. */
export interface ToolSelection {
  tool_name: string;
  user_fields?: Record<string, unknown>;
}

/** Mirrors backend `LoopPolicy` — loop convergence policy. */
export interface LoopPolicy {
  rollover_enabled?: boolean;
  idle_timeout_s?: number;
  summarize_steps_span?: number;
}

/** Mirrors backend `CapabilitySetRecord` — reusable execution + prompt bundle.
 *
 * The backend splits Actor into Actor + CapabilitySet so that the same
 * capability/workspace/policy bundle can be shared across actors with
 * different personas. Tools are explicitly listed in `tools`; integration
 * references use `integration_ids` (FK to IntegrationRecord.id).
 */
export interface CapabilitySetResource extends Resource {
  name: string;
  description: string;
  workspace_path: string;
  tools: ToolSelection[];
  integration_ids: string[];
  loop_policy: LoopPolicy;
}

export interface ActorResource extends Resource {
  name: string;
  type: string;
  persona_prompt: string;
  capability_set_id: string;
  capability_set?: CapabilitySetResource;
  llm_backend_id: string;
  model: string;
  per_run_budget?: {
    max_steps: number;
    max_tokens: number;
    max_usd: number;
  };
  generation_override?: GenerationParams;
  config?: Record<string, unknown>;
  skill_scope?: "local_only" | "global_and_local";
}

export interface SkillInfo {
  name: string;
  source: "global" | "local" | string;
  path: string;
  content?: string;
}

export interface ActorSkillsView {
  global_skills: SkillInfo[];
  local_skills: SkillInfo[];
  loaded_skills: SkillInfo[];
}

export interface ActorIngressRuleResource extends Resource {
  /** Source ID glob pattern (e.g. "qq:*"). */
  source_id_pattern: string;
  /** Source path glob pattern (e.g. "**"). */
  source_path_pattern: string;
  /** Kind patterns (e.g. ["text", "image:*"]). */
  kind_patterns: string[];
  /** Target actor FK. */
  actor_id: string;
  /** Resolved actor reference (when eagerly loaded). */
  actor?: {
    id: string;
    name: string;
  };
}

export interface IntegrationResource extends Resource {
  name: string;
  config?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Plugin shapes (admin-specific)
// ---------------------------------------------------------------------------

export interface PluginInfo {
  name: string;
  version: string;
  description: string;
  entry: string;
  installed: boolean;
  integration_id: string;
  enabled: boolean;
}

export interface PluginListResponse {
  status: string;
  plugins: PluginInfo[];
}

// ---------------------------------------------------------------------------
// Daemon admin refresh event
// ---------------------------------------------------------------------------

export interface ResourceChangedEvent {
  table: string;
  action: "inserted" | "updated" | "deleted";
  row_ids: string[];
  changed_fields?: string[];
}

// ---------------------------------------------------------------------------
// Live capabilities (reflects actual integration instances, not factory kinds)
// ---------------------------------------------------------------------------

/** A capability from an existing integration record (enabled or disabled).
 *
 * Returned by GET /api/live-capabilities.
 * Unlike IntegrationKind.capabilities (static factory declarations),
 * these reflect actual integration instances in the database.
 */
export interface LiveCapability {
  capability_id: string;
  capability_name: string;
  description: string;
  namespace: string;
  integration_id: string;
  integration_name: string;
  enabled: boolean;
}

export interface LiveCapabilitiesResponse {
  capabilities: LiveCapability[];
}

// ---------------------------------------------------------------------------
// Admin Conversation types
// ---------------------------------------------------------------------------

export interface ConversationData {
  conversation_id: string;
  title: string;
  actor_id: string;
  capability_set_id: string;
  llm_backend_id: string;
  model: string;
  created_at?: string;
  updated_at?: string;
}

export interface ConversationCreateResponse {
  status: string;
  data: ConversationData;
}

export interface SendMessageResponse {
  status: string;
  data: {
    conversation_id: string;
    message_id: string;
  };
}

export interface ConversationUploadedFile {
  name: string;
  path: string;
  url: string;
  size: number;
  content_type: string;
}

export interface ConversationUploadResponse {
  status: string;
  data: ConversationUploadedFile[];
}

export interface CancelTurnResponse {
  status: string;
  data: {
    conversation_id: string;
    cancelled: boolean;
    pending?: boolean;
  };
}

export interface ConversationListItem {
  conversation_id: string;
  title: string;
  actor_id: string;
  created_at?: string;
  updated_at?: string;
}

export interface ConversationListResponse {
  status: string;
  data: ConversationListItem[];
}

export interface ConversationMessage {
  id: number;
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system" | "tool";
  raw_content: string;
  metadata: Record<string, unknown>;
  timestamp: number;
}

export interface ConversationMessagesResponse {
  status: string;
  data: ConversationMessage[];
}

export interface ConversationSSEBaseEvent {
  conversation_id: string;
  event_id: string;
  sequence: number;
  event_type: string;
  timestamp: number;
}

export interface TurnStartedEvent extends ConversationSSEBaseEvent {
  event_type: "turn_started";
  turn_id: string;
  agent_id: string;
  agent_name: string;
}

export type TranscriptDelta =
  | {
      type: "thinking";
      text_delta: string;
    }
  | {
      type: "text";
      text_delta: string;
    }
  | {
      type: "tool_call";
      tool_call_id: string;
      tool_name?: string;
      arguments_delta?: unknown;
      arguments_text_delta?: string;
    }
  | {
      type: "tool_result";
      tool_call_id: string;
      tool_name?: string;
      stream?: "stdout" | "stderr" | "combined";
      text_delta: string;
    }
  | {
      type: "error";
      text_delta: string;
    };

export interface TranscriptDeltaEvent extends ConversationSSEBaseEvent {
  event_type: "transcript_delta";
  turn_id: string;
  deltas: TranscriptDelta[];
}

export interface TurnCompletedEvent extends ConversationSSEBaseEvent {
  event_type: "turn_completed";
  turn_id: string;
}

export interface ConversationErrorEvent extends ConversationSSEBaseEvent {
  event_type: "error";
  turn_id?: string;
  error: string;
}

/** SSE event from /api/admin/conversations/{id}/events */
export type ConversationSSEEvent =
  | TurnStartedEvent
  | TranscriptDeltaEvent
  | TurnCompletedEvent
  | ConversationErrorEvent;
