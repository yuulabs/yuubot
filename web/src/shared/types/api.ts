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

export interface ActorSnapshot {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  status: string;
  last_error: Record<string, unknown> | null;
  workspace: string;
  model: ModelSelector | null;
  context_compression_tokens: number;
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
  health_status?: string;
  health_reason?: string;
  health_details?: Record<string, unknown>;
  action_hint?: Record<string, unknown> | null;
}

export type IntegrationDetail = IntegrationSnapshot;

export interface IntegrationConfigInput {
  name?: string;
  config: Record<string, unknown>;
}

export interface RouteRecord {
  id: string;
  integration_type: string;
  pattern: string;
  actor_id: string;
  enabled: boolean;
}

export interface RouteCreateInput {
  id?: string;
  integration_type?: string;
  pattern: string;
  actor_id: string;
  enabled?: boolean;
}

export type RouteUpdateInput = Omit<RouteRecord, "id">;

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
  last_input_tokens?: number;
  last_cached_input_tokens?: number;
  last_output_tokens?: number;
}

export interface BootstrapSnapshot {
  auth?: {
    surface: "local_dev" | "local_admin" | "trusted_admin" | "public";
    mode: "none" | "proxy" | "builtin" | "loopback_bypass";
    method?: "proxy" | "builtin_session" | "loopback_bypass" | null;
    csrf_header: string;
  };
  development?: boolean;
  public_url_base?: string;
  schema_version: number;
  workspace_dir: string;
  actors: ActorSnapshot[];
  integrations: IntegrationSnapshot[];
  routes: RouteRecord[];
}

export interface ActorRecord {
  id: string;
  name: string;
  description?: string;
  workspace?: string;
  persona?: string;
  model: ModelSelector;
  context_compression_tokens?: number;
}

export type ActorInput = Omit<ActorRecord, "id">;

export type ModelSelector =
  | { type: "alias"; alias: string }
  | { type: "exact"; endpoint_id: string; model: string };

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

export type McpAuthMode = "none" | "api_key" | "oauth_auto" | "oauth_manual" | "auto" | "oauth";
export type McpTransport = "http" | "stdio";
export type McpServerStatus = "disabled" | "checking" | "needs_auth" | "ready" | "degraded" | "error";

export interface McpServerBody {
  name: string;
  endpoint_url: string;
  transport: McpTransport;
  auth_mode: McpAuthMode;
  enabled: boolean;
  api_key?: string;
  api_key_header?: string;
  api_key_prefix?: string;
  oauth_issuer?: string;
  oauth_authorization_endpoint?: string;
  oauth_token_endpoint?: string;
  oauth_client_id?: string;
  oauth_client_secret?: string;
  oauth_scope?: string;
}

export interface McpServerState {
  status: McpServerStatus;
  capabilities_summary?: string;
  last_error?: string | null;
  action_hint?: Record<string, unknown> | null;
  last_checked_at?: string | null;
}

export interface McpServerSnapshot extends McpServerState {
  id: string;
  name: string;
  endpoint_url: string;
  transport: McpTransport;
  auth_mode: McpAuthMode;
  oauth_issuer?: string;
  oauth_authorization_endpoint?: string;
  oauth_token_endpoint?: string;
  oauth_client_id?: string;
  oauth_scope?: string;
  credential_configured: boolean;
  enabled: boolean;
  tools_count: number;
  resources_count: number;
  prompts_count: number;
}

export interface AuthAttempt {
  id: string;
  connection_id: string;
  method: "oauth_pkce" | "device_code" | "api_key" | "manual";
  status: "waiting_for_user" | "polling" | "exchanging" | "succeeded" | "failed" | "expired";
  action: Record<string, unknown>;
  error?: string | null;
  expires_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface CredentialRecord {
  id: string;
  owner_scope: "global";
  kind: "oauth_token" | "api_key" | "manual_token";
  provider: string;
  label: string;
  redacted_summary: string;
  expires_at?: string | null;
  scopes: string[];
  secret_ref: string;
  created_at?: string;
  updated_at?: string;
}

export interface SkillSummary {
  id: string;
  name: string;
  description: string;
  scope: "global";
  inspect_hint: string;
  source: "builtin" | "custom" | "package";
  can_edit: boolean;
  can_update: boolean;
  can_delete: boolean;
  can_copy: boolean;
  error: string;
}

export interface SkillRecord {
  id: string;
  name: string;
  description: string;
  scope: "global";
  body: string;
  created_at?: string;
  updated_at?: string;
  source: "builtin" | "custom" | "package";
  source_path?: string;
}

export type SkillInput = Pick<SkillRecord, "name" | "description" | "body" | "scope">;

export interface SkillCopyFile {
  path: string;
  status: "added" | "deleted" | "modified" | "unchanged";
  binary: boolean;
  diff: string;
}

export interface SkillCopyPreview {
  skill_id: string;
  actor_id: string;
  path: string;
  exists: boolean;
  conflict: boolean;
  up_to_date: boolean;
  files: SkillCopyFile[];
}

export interface SkillCopyBody {
  actor_id: string;
  replace: boolean;
}

export interface SkillPackageBody {
  source: string;
  skills: string[];
  agents: string[];
  copy: boolean;
}

export interface SkillPackageResult {
  action: "add" | "remove" | "update";
  target: string;
  command: string[];
  exit_code: number;
  stdout: string;
  stderr: string;
  warning: string;
}

export interface HistoryItem {
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface ConversationHistoryResponse {
  conversation_id: string;
  items: HistoryItem[];
  has_more: boolean;
  first_seq: number | null;
  last_seq: number | null;
}

export interface ConversationUsageRecord {
  conversation_id: string;
  seq: number;
  usage: Record<string, unknown>;
  account: Record<string, unknown>;
  created_at: string;
}

export interface HostStats {
  cpu_percent: number;
  memory_used_bytes: number;
  memory_total_bytes: number;
  memory_percent: number;
  disk_used_bytes: number;
  disk_total_bytes: number;
  disk_free_bytes: number;
  disk_percent: number;
  disk_path: string;
  net_bytes_sent: number;
  net_bytes_recv: number;
}

export interface RuntimeSnapshot {
  data_dir: string;
  workspace_dir: string;
  host: HostStats;
  tasks: TaskRecord[];
  actors: Array<{ id: string; status: string; mailbox: string }>;
  integrations: Array<{ name: string; package_path: string }>;
  events: RuntimeEvent[];
}

export interface RuntimeEvent {
  ts: string;
  kind: string;
  title: string;
  detail?: string;
  context: Record<string, unknown>;
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
  delivery?: "manual" | "conversation" | "actor";
  delivery_state?: string;
  interactive?: boolean;
  stdout_tail?: string;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  metadata?: Record<string, unknown>;
}

export interface CronScheduleRecord {
  kind: "cron" | "at";
  timezone: string;
  cron?: string | null;
  at?: string | null;
}

export interface CronActionRecord {
  kind: "shell" | "wakeup" | "actor_message" | "conversation_callback" | "reminder";
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

export interface UpdateStatus {
  supported: boolean;
  install_kind: string;
  current_version: string;
  current_commit?: string | null;
  remote_commit?: string | null;
  update_available: boolean;
  message?: string;
}

export interface UpdateApplyResult {
  status: string;
  log_path?: string | null;
  message?: string;
}
