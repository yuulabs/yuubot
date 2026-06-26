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

// ---------------------------------------------------------------------------
// Resource type literals (matches daemon URL slugs)
// ---------------------------------------------------------------------------

export type ResourceType =
  | "llm-backends"
  | "characters"
  | "capability-sets"
  | "actors"
  | "ingress-rules"
  | "integrations"
  | "prompt-templates";

// ---------------------------------------------------------------------------
// Resource records (mirror msgspec struct shapes returned by the API)
// ---------------------------------------------------------------------------

/** Base fields shared by resource records.
 *
 * `enabled` is optional because CharacterRecord and LLMBackendRecord do not
 * carry an `enabled` field in the backend schema.
 */
export interface Resource {
  id: string;
  enabled?: boolean;
  version?: number;
  created_at?: string;
  updated_at?: string;
}

export interface CharacterResource extends Resource {
  name: string;
  description: string;
  system_prompt: string;
  facade_module: string;
  default_hints: {
    language: string;
    tone: string;
  };
  is_builtin: boolean;
  builtin_version: string;
  cloned_from: string;
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

/** Mirrors backend `ModelCatalog` struct. */
export interface ModelCatalog {
  names: string[];
}

/** Mirrors backend `PricingTable` / `PricingEntry`. */
export interface PricingEntry {
  model: string;
  input_per_million?: number;
  cached_input_per_million?: number;
  output_per_million?: number;
}

/** Mirrors backend `BudgetPolicy`. */
export interface BudgetPolicy {
  daily_usd?: number | null;
  monthly_usd?: number | null;
}

/** Mirrors backend `LLMProviderOptions`. */
export interface LLMProviderOptions {
  base_url?: string;
  provider_name?: string;
  api_key?: string;
  timeout?: number;
  max_retries?: number;
}

/** Mirrors backend `StreamOptions`. */
export interface StreamOptions {
  model?: string;
  max_tokens?: number | null;
  temperature?: number | null;
  top_p?: number | null;
}

export interface LLMBackendResource extends Resource {
  name: string;
  /** yuuagents provider key (e.g. "openai", "anthropic", "deepseek"). */
  yuuagents_provider: string;
  /** Capability flags for default model. REQUIRED by backend. */
  model_capabilities: ModelCapabilities;
  /** Available model names. REQUIRED by backend. */
  models: ModelCatalog;
  /** Per-model pricing. REQUIRED by backend. */
  pricing: { entries: PricingEntry[] };
  /** Budget limits. REQUIRED by backend. */
  budget: BudgetPolicy;
  /** Provider-level options (base URL, timeout, retries, api_key). */
  provider_options?: LLMProviderOptions;
  /** Default model for agent runs. */
  default_model?: string;
  /** Default completion parameters. */
  default_stream_options?: StreamOptions;
}

/** Mirrors backend `CapabilitySetRecord` — reusable execution + prompt bundle.
 *
 * The backend splits Actor into Actor + CapabilitySet so that the same
 * capability/workspace/policy bundle can be shared across actors with
 * different characters. Only MVP fields are typed here; advanced fields
 * (agent_tools, tool_ids, skills, prompt_fragments, permission_limits,
 * bootstrap_path) use backend defaults and are omitted from the UI.
 */
export interface CapabilitySetResource extends Resource {
  name: string;
  description: string;
  integration_capability_ids: string[];
  workspace_path: string;
  runtime_policy: {
    memory_enabled: boolean;
  };
  resource_policy: {
    budget_usd_daily?: number | null;
    concurrency_limit?: number;
  };
}

export interface ActorResource extends Resource {
  name: string;
  type: string;
  /** Default model name (e.g. "gpt-4o"). Renamed from `model`. */
  default_model: string;
  /** Resolved character reference (eagerly loaded by the daemon). */
  default_character?: {
    id: string;
    name: string;
    description: string;
  };
  /** Resolved capability set reference (eagerly loaded by the daemon). */
  capability_set?: CapabilitySetResource;
  /** Resolved LLM backend reference. */
  default_llm_backend?: {
    id: string;
    name: string;
    yuuagents_provider: string;
  };
  /** Agent budget guardrails (nested; was flat `max_steps` / `daily_budget`). */
  default_budget?: {
    max_steps: number;
    max_tokens: number;
    max_usd: number;
  };
  /** Actor-level LLM overrides. */
  default_llm_options?: {
    max_tokens: number | null;
    stream_options: StreamOptions;
  };
  config?: Record<string, unknown>;
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

export interface PromptTemplateResource extends Resource {
  name: string;
  description: string;
  content: string;
  is_builtin: boolean;
  builtin_version: string;
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
  character_id: string;
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

export interface CancelTurnResponse {
  status: string;
  data: {
    conversation_id: string;
    cancelled: boolean;
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
