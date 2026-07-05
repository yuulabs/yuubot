export interface ApiError {
  code?: string;
  detail?: string;
  message?: string;
  reason?: string;
}

export interface HealthResponse {
  ok?: boolean;
  status?: string;
}

export interface ProviderSnapshot {
  id: string;
  name: string;
  protocol: string;
  configured: boolean;
  last_error: string | null;
  model_count: number;
  configured_model_count: number;
}

export interface ProviderProtocolSpec {
  protocol: string;
  title: string;
  default_endpoint: string;
  config_schema: Record<string, unknown>;
  secret_fields: string[];
}

export interface ProviderInput {
  name: string;
  protocol: string;
  config: Record<string, unknown>;
}

export interface ProviderDetail extends ProviderSnapshot {
  config: Record<string, unknown>;
  model_cards: ModelCard[];
}

export interface ValidationResult {
  ok: boolean;
  message?: string;
  detail?: Record<string, unknown>;
}

export interface AccountSnapshot {
  available?: boolean;
  balance?: number | null;
  currency?: string | null;
  raw?: Record<string, unknown>;
}

export interface ActorSnapshot {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  status: string;
  last_error: Record<string, unknown> | null;
  workspace: string;
  provider: string;
  model: ModelCard;
}

export interface IntegrationSnapshot {
  type: string;
  name: string;
  package_path: string;
  enabled: boolean;
  configured: boolean;
  last_error: Record<string, unknown> | null;
  config_schema: Record<string, unknown>;
  config: Record<string, unknown>;
}

export type IntegrationDetail = IntegrationSnapshot;

export interface RouteRecord {
  id: string;
  integration_type: string;
  pattern: string;
  actor_id: string;
  enabled: boolean;
}

export interface ConversationSummary {
  id: string;
  actor_id?: string;
  title?: string;
  status?: string;
  created_at?: string;
  last_active_at?: string | null;
  last_error?: Record<string, unknown> | null;
  message_count?: number;
  last_seq?: number | null;
}

export interface BootstrapSnapshot {
  development?: boolean;
  schema_version: number;
  providers: ProviderSnapshot[];
  actors: ActorSnapshot[];
  integrations: IntegrationSnapshot[];
  routes: RouteRecord[];
  conversations: ConversationSummary[];
}

export interface ModelCard {
  selector: string;
  reasoning_effort?: string;
  vision?: boolean;
  toolcall?: boolean;
  json?: boolean;
  input_price_per_million?: number;
  cached_input_price_per_million?: number;
  output_price_per_million?: number;
}

export interface ActorRecord {
  id: string;
  name: string;
  description?: string;
  workspace?: string;
  persona?: string;
  model: ModelCard;
  provider: string;
}

export interface ActorInboundBody {
  text: string;
  conversation_id?: string;
  metadata?: Record<string, unknown>;
}

export interface ActorInboundResponse {
  actor_id?: string;
  conversation_id?: string;
  delivered?: boolean;
  queued?: boolean;
}

export interface WorkspaceEntry {
  name: string;
  path: string;
  kind: "file" | "directory";
  size?: number;
  mtime?: string | null;
  mime?: string;
}

export interface WorkspaceDirectorySnapshot {
  path: string;
  entries: WorkspaceEntry[];
}

export interface UploadResponse {
  files: Array<Record<string, unknown>>;
}

export interface KvDocument {
  actor_id: string;
  key: string;
  value: unknown;
  etag: string;
  updated_at?: string;
}

export interface KvPutBody {
  value: unknown;
}

export interface EtagResponse<T> {
  data: T;
  etag: string | null;
}

export interface IntegrationRecord {
  id: string;
  type: string;
  name: string;
  config?: Record<string, unknown>;
}

export interface HistoryItem {
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface ConversationCostRecord {
  conversation_id: string;
  seq: number;
  usage: Record<string, unknown>;
  account: Record<string, unknown>;
  estimated: boolean;
  created_at: string;
}

export interface RuntimeSnapshot {
  data_dir: string;
  workspace_dir: string;
  tasks: TaskRecord[];
  actors: Array<{ id: string; status: string; mailbox: string }>;
  integrations: Array<{ name: string; package_path: string }>;
  events: Array<{ ts: number; kind: string; payload: Record<string, unknown> }>;
}

export interface TaskRecord {
  id: string;
  owner: string;
  kind: string;
  name: string;
  intro?: string;
  status: string;
  error?: string | null;
  exit_code?: number | null;
  delivery_state?: string;
  stdout_tail?: string;
  created_at?: string;
  updated_at?: string;
}

export interface CronScheduleRecord {
  kind: "cron" | "at";
  timezone: string;
  cron?: string | null;
  at?: string | null;
}

export interface CronActionRecord {
  kind: "shell" | "wakeup" | "reminder";
  name?: string;
  shell?: string;
  intro?: string;
  text?: string;
  conversation_id?: string | null;
  title?: string;
  body?: string;
  channels?: Array<{ kind: string; config?: Record<string, unknown> }>;
}

export interface CronJobRecord {
  id: string;
  owner: string;
  name: string;
  schedule: CronScheduleRecord;
  action: CronActionRecord;
  status: "active" | "paused" | "completed" | "cancelled";
  next_run_at?: string | null;
  last_run_at?: string | null;
  once?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface PushSubscriptionRecord {
  id: string;
  endpoint: string;
  keys: Record<string, string>;
  created_at?: string;
  updated_at?: string;
}

export interface ShareGrant {
  id: string;
  actor_id: string;
  source_path: string;
  url?: string;
  revoked?: boolean;
  kind?: "file" | "directory";
  entry_path?: string;
  created_at?: string;
  updated_at?: string;
  expires_at?: string | null;
}

export interface ItemsResponse<T> {
  items: T[];
}
