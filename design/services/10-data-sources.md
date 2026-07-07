# Design: Data Sources, MCP, Coding CLI, and Skills

**实现顺序：10**（依赖 [02-admin-boundary.md](02-admin-boundary.md)、
[04-tasks.md](04-tasks.md)、[08-python-kernel.md](08-python-kernel.md)）

## Scenario

yuubot 是个人 agent hub：daemon 管理外部连接、登录状态、能力发现与 Admin UX；LLM 主要通过
`execute_python` 组合调用大量 CRUD / data source API，而不是把每一步都拆成 LLM tool-call。

核心路径：

```text
User asks for data work
  -> Actor conversation
    -> execute_python
      -> yb facade calls daemon
        -> daemon uses managed credentials / connections
          -> external MCP / SaaS / CLI capability
```

设计目标：

- 登录鉴权由 daemon / Admin UI 托管，secret 不进入 prompt。
- prompt 只暴露必要发现入口，不默认展开远端方法细节。
- MCP 是主 data source 总线；SaaS 优先通过 MCP 接入。
- MCP 集成采用现成 SDK / adapter 设计，不自研 JSON-RPC、transport、session、schema
  validation。yuubot 只做 connection 管理、credential 托管、能力缓存和 Python facade。
- Coding CLI 隶属于 Integration 范围，使用原生登录流；Admin UI 提供服务器侧 PTY 解决
  headless 登录（没有无头登陆的 coding cli 直接让它爬）。
- Skill 是按需加载的 SOP / 规范 / 工作流知识，不是 data source。

参考：

- MCP introduction: <https://modelcontextprotocol.io/docs/getting-started/intro>
- MCP remote server connection: <https://modelcontextprotocol.io/docs/develop/connect-remote-servers>
- MCP authorization: <https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization>
- MCP Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
- Hermes MCP design: <https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp>
- Pi MCP adapter: <https://pi.dev/packages/pi-mcp-adapter>
- Agent Skills: <https://agentskills.io/home>

## Concepts

```text
MCP Server       = 外部能力端点；remote HTTP 和 stdio 都走 SDK client
Connection       = daemon 对某个外部能力端点的配置、credential 绑定与运行态
Credential       = daemon 加密托管的 OAuth token / API key / refresh token 等
Integration      = yuubot 内部增强能力；负责本地/产品特定 readiness probe 与 facade
Coding CLI       = Codex / Opencode / Cursor / Claude Code 等官方 CLI
Admin PTY        = 管理员在浏览器中操作服务器侧终端，用于原生 CLI 登录与诊断
Skill            = SOP / 规范 / 行动指南；默认只暴露 name / description
Capability Index = daemon 缓存的 MCP tools/resources/prompts 轻量目录，不等同完整 schema
```

不要把三类能力混成同一抽象：

| 类型 | 主要职责 | 登录归属 | LLM 调用面 |
| --- | --- | --- | --- |
| MCP | 标准 data source / tool / resource 总线 | daemon Credential | `yb.mcps` |
| Coding CLI Integration | 本地编码 agent readiness 与任务入口 | 官方 CLI 写入系统用户 HOME | concrete `yext.*` integration facade |
| Skill | 行为规范、SOP、领域流程 | 无 runtime credential | prompt 按需展开 |

## MCP

MCP 是 data source 主路径。daemon 是 MCP host/client；yuubot v1 不把自己暴露为 MCP server。

实现原则：

- 使用官方 `mcp` Python SDK 的 client、transport、session、schema 类型与 call/read/list API。
  当前生产线优先 pin 稳定 v1.x；v2 仍是预发布时不作为默认依赖。
- 不实现自己的 MCP JSON-RPC router。daemon 只包一层 `McpConnectionManager`，负责把
  yuubot 的 durable records、credential、Admin 状态和 SDK client session 对起来。
- 不把每个 MCP tool 自动注册成 LLM tool。默认采用 proxy/facade 模式，LLM 通过
  `yb.mcps.search()`、`get_spec()`、`invoke()` 渐进发现。
- 借鉴 Hermes / Pi：支持启动时发现、缓存 metadata、按服务器过滤工具、lazy/eager
  连接生命周期，以及可选 direct tool 暴露；v1 默认只实现 proxy/facade。

边界转换只有一件事：

```text
MCP SDK result / JSON-RPC-level schema
  -> daemon normalizes status, cache, errors, oversized output
    -> yb.mcps facade exposes Pythonic async API
```

### Supported transports

v1 支持两类连接记录，但不自己写 transport：

```text
remote HTTP / Streamable HTTP
  -> SDK HTTP client owns MCP protocol exchange
  -> daemon injects auth headers/tokens and manages lifecycle
  -> Python facade uses daemon local RPC

stdio
  -> SDK stdio client owns subprocess transport
  -> daemon resolves command/args/env/cwd and process lifetime
  -> Python facade sees the same McpClient surface
```

remote HTTP 是 SaaS 首选；stdio 是本地工具生态的兼容入口。二者不分裂 facade。

Connection config 尽量兼容业界已有形状，便于从 `.mcp.json`、Claude Desktop、Cursor、
Hermes/Pi 等配置导入：

```yaml
mcp_servers:
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    env: {}
    enabled: true
    tools:
      allow: ["read_file", "list_directory"]

  linear:
    url: "https://mcp.linear.app/mcp"
    auth: "oauth"
    enabled: true
```

yuubot durable record 可以是规范化后的内部字段，但 Admin import/export 应保留这种常见
config mental model。

### Remote MCP auth

Remote HTTP MCP 的标准路径由 SDK + OAuth helper 承担协议细节；daemon 只持久化 attempt
和 credential：

```text
Admin adds MCP server URL
  -> daemon creates/loads McpConnectionRecord
  -> SDK probes MCP endpoint
    -> if public: initialize and discover capabilities
    -> if auth required:
         SDK/helper performs MCP auth discovery where available
         daemon creates OAuth attempt with PKCE / provider-specific config
         return Admin action
  -> Admin opens browser auth URL
    -> callback returns to yuubot public/admin URL
      -> daemon exchanges code, stores encrypted token
        -> SDK reconnects and daemon refreshes capability index
```

Auth modes:

| Mode | 用途 |
| --- | --- |
| `none` | public MCP server |
| `api_key` | bearer/header/env key；适配非标准 vendor server |
| `oauth_auto` | MCP spec discovery + OAuth 2.1 / PKCE |
| `oauth_client` | Admin 提供预注册 client_id/client_secret/scope；用于不支持 DCR 的 provider |
| `oauth_manual` | Admin 手填 auth/token endpoint、resource、scope 等 escape hatch |
| `mtls` | 可选后补：client cert/key 交给 SDK/http client |

Client identity 默认使用 yuubot 部署 URL 暴露 metadata / callback。若 server 不支持 metadata
或 discovery，可 fallback 到 dynamic registration 或 manual client 配置。

Headless / remote host auth 允许两种完成方式：

- Admin UI 打开 authorize URL，正常回调到 yuubot callback。
- 若外部 provider 只能回调 localhost 或临时 URL，Admin 粘贴最终 redirect URL / `code` 到
  Admin UI，由 daemon 完成 exchange。

### Records and state

Durable record 存 metadata，不存明文 secret：

```text
McpServerRecord:
  id
  name
  transport: "http" | "stdio"
  url?
  command?
  args
  env_secret_refs
  cwd?
  headers_secret_refs
  auth_mode: "none" | "api_key" | "oauth_auto" | "oauth_client" | "oauth_manual" | "mtls"
  credential_id?
  lifecycle: "lazy" | "eager" | "keep_alive"
  tool_filter
  output_guard
  timeout_ms?
  connect_timeout_ms?
  enabled
  created_at
  updated_at
```

兼容性导入可以先落原始配置，再规范化：

```text
McpConfigImport:
  source: ".mcp.json" | "claude_desktop" | "cursor" | "manual" | "hermes" | "pi"
  raw_config_json
  imported_server_ids
  created_at
```

Runtime state 由 daemon connection manager 维护：

```text
McpServerState:
  status: "disabled" | "disconnected" | "checking" | "needs_auth" | "ready" | "degraded" | "error"
  lifecycle_state: "idle" | "connecting" | "connected" | "disconnecting"
  capabilities_summary
  last_error
  action_hint?
  last_checked_at
  cache_updated_at?
```

Credential secret payload 经 encrypted store 读写，Admin/bootstrap 只返回 redacted summary、
expiry、scope、configured flag、last_error。

### Lifecycle and caching

借鉴 Pi adapter 的 lifecycle model：

| Mode | 行为 |
| --- | --- |
| `lazy` | 默认。启动不连接；首次调用或 Admin reconnect 时连接；idle timeout 后断开。metadata cache 仍可 search/list。 |
| `eager` | daemon 启动或 enable 后连接；掉线不强制自动重连。 |
| `keep_alive` | daemon 启动连接，并通过 health check / retry 保持长期可用。 |

缓存层保存：

```text
McpCapabilityCache:
  server_id
  protocol_version?
  tools_json
  resources_json
  prompts_json
  fetched_at
  source_fingerprint
```

`search()` 读缓存即可工作。第一次没有缓存时返回 `needs_connect` 摘要，Admin 和 facade 都可以触发
`refresh()`。

### Capability discovery

daemon 在连接 ready 后获取并缓存轻量 index：

```text
server
  tools: name, description
  resources: uri/name, description, mime-ish hints
  prompts: name, description
```

`search()` 只返回摘要，不返回参数说明。调用方需要具体参数时再 `get_spec()`。

按服务器工具过滤：

```text
tool_filter:
  mode: "all" | "allow" | "deny"
  names: list[str]
```

过滤同时作用于 search/list/get_spec/invoke，避免 Admin 以为隐藏的工具仍可被 LLM 通过名字调用。

可选 direct exposure：

```text
direct_tools: false | true | list[str]
```

v1 默认 `false`。若未来开启 direct tools，仍从 metadata cache 注册，避免启动时必须连接所有
MCP server。large server 继续走 facade/proxy，因为每个 direct tool 都会消耗 prompt tokens。

### Python facade

LLM 使用 Python 组合 MCP 调用：

```python
import yb.mcps

matches = await yb.mcps.search("linear open bugs")
linear = yb.mcps.get_client("linear")
print(await linear.get_spec("search_issues"))
result = await linear.invoke("search_issues", query="state:open bug", limit=20)
```

Facade shape:

```text
yb.mcps.search(query: str = "", *, kind: str = "", server: str = "") -> list[McpSearchResult]
yb.mcps.get_client(server_id: str) -> McpClient

McpClient:
  refresh() -> McpServerState
  list_tools() -> list[McpCapabilitySummary]
  list_resources() -> list[McpCapabilitySummary]
  list_prompts() -> list[McpCapabilitySummary]
  get_spec(name: str) -> str
  invoke(name: str, **kwargs) -> McpResult
  read_resource(uri: str) -> McpResult
  render_prompt(name: str, **kwargs) -> McpResult
```

`get_spec()` 不返回完整 JSON Schema。它把 MCP tool input schema 压缩成人类可读 Python 签名，
面向 LLM 调用，不追求完全精确，做好编码复杂度的trade-off：

```text
search_issues(query: str, limit: int = 20) -> McpResult
Search issues in Linear.
```

粗略转换规则：

| JSON Schema | Signature |
| --- | --- |
| `string` | `str` |
| `integer` | `int` |
| `number` | `float` |
| `boolean` | `bool` |
| `array` | `list` |
| `object` / nested object | `dict` |
| short enum | `Literal[...]` |
| unknown / complex | `Any` |

required 字段无默认；optional 字段使用 schema default 或 `None`。长 description 截断。

`McpResult` 面向 Python 使用者，而不是复刻 JSON-RPC response：

```text
McpResult:
  content: list[McpContentBlock]
  text: str
  structured: dict[str, object] | list[object] | None
  artifacts: list[McpArtifact]
  is_error: bool
  error_message?
  raw_ref?
```

输出保护默认开启，借鉴 Pi 的 output guard：

- 大文本只内联 head preview，完整内容写入 actor workspace 或 daemon temp artifact，并返回路径/ref。
- raw MCP result 只在小于阈值时保留在 `raw_ref` 可取的位置；不会默认塞进 conversation history。
- image/file/content block 保留结构化引用，不转换成巨大的字符串。

### Prompt exposure

developer prompt 只说明 MCP 入口，不注入所有远端 schema：

```text
MCP data sources are available through `yb.mcps`.
Use `await yb.mcps.search(query)` to discover relevant servers/tools/resources.
Search results intentionally omit parameter details.
Before calling a tool, use `await client.get_spec(name)` and follow the Python signature.
Call tools with `await client.invoke(name, **kwargs)`.
Secrets and raw credentials are managed by daemon and are never available.
```

若没有 enabled MCP server，则 prompt 明确写 “No MCP servers are currently configured.”

### Admin UI

新增专门的 MCP 页面，功能边界参考 Hermes / Pi：

- add server: URL、command/args、import existing `.mcp.json` / common host config。
- status panel: disabled / disconnected / needs_auth / ready / degraded / error。
- actions: connect, reconnect, refresh metadata, login/reauth, disable, delete。
- auth panel: open authorize URL；或粘贴 redirect URL/code 完成 headless auth。
- tool list: tools/resources/prompts count、搜索、allow/deny 过滤。
- lifecycle: lazy/eager/keep_alive、idle timeout、request timeout。
- output guard: 默认开启，可调阈值。
- raw diagnostics: last error、SDK/protocol version、last cache time；不展示 secret。

## Coding CLI Integrations

Coding CLI 不是标准远程 OAuth data source。不同 CLI 的登录方式、config path、浏览器回调、
token 存储都不统一。yuubot 不重写这些登录协议。

### Admin PTY

Admin UI 提供普通服务器侧 PTY：

```text
Admin opens Terminal
  -> browser WebSocket
    -> daemon forks PTY as yuubot server user
      -> user runs official CLI login command
        -> CLI writes normal config under system user HOME
```

约定：

- PTY 是 Admin 工具，不是 LLM tool。
- PTY 使用服务器运行用户的真实 HOME；不创建隔离 profile。
- PTY 只依赖 AdminAuth；不做命令白名单。
- daemon 可记录 audit metadata：auth user、started_at、closed_at、cwd、exit status。
- LLM 不能直接打开或驱动交互式 PTY。

### Readiness probe

Coding CLI Integration 负责检测而不是登录：

```text
Admin enables Codex integration
  -> daemon checks binary + login/config readiness
    -> ready: expose facade
    -> needs_action: Admin UI shows Open Terminal + suggested command
```

状态：

```text
IntegrationState:
  status: "disabled" | "checking" | "ready" | "needs_action" | "degraded" | "error"
  reason
  action_hint?
  last_checked_at
```

Action hint 示例：

```json
{
  "kind": "open_pty",
  "title": "Login to Codex",
  "suggested_command": "codex login",
  "cwd": "~"
}
```

Probe 触发点：

- enable integration
- daemon startup
- Admin clicks recheck
- before first facade call
- after CLI call failure

文件 watch 可作为 UI 优化，但正确性依赖 probe。

### LLM behavior

LLM 通过 Python facade 请求 coding capability；未登录时返回可操作错误：

```text
Codex is not logged in. Ask the admin to open Terminal and run `codex login`.
```

LLM 不知道 token、config path、browser callback，也不接触 PTY。

## SaaS Native Integrations

大多数 SaaS data source 优先通过 MCP 接入。Native Integration 只用于 MCP 不足的情况：

- yuubot 需要产品特定 runtime 行为。
- webhook/inbound 需要平台验签或路由。
- local / CLI / agent capability 不适合 MCP。
- SaaS 没有可用 MCP，且 facade 能显著提升组合效率。

Native SaaS OAuth 可复用 MCP 的 Credential/AuthAttempt 基础设施，但 connection type 不同。

## Skills

Skill 表示 “做某类事情时应遵循的规范 / SOP / workflow”。它不是 credential，不是 data source，
也不是 remote tool。

默认 prompt 只列：

```text
skill name
short description
where/how to inspect full instructions
```

按需加载完整 `SKILL.md`，避免把所有 SOP 全塞进系统 prompt。Skill 可引用 MCP、Integration、
workspace 文件或其他工具，但自身不拥有 runtime secret。

Admin UI 管理全局 Skill CRUD。v1 默认全局可见；record 预留 `scope="global"` 以便未来扩展到
actor/workspace/user。

## Auth and Persistence

### Credential store

新 credential store 复用现有 AES-GCM master key 思路，但从 legacy import 私有实现中提升为
runtime 基础设施。

```text
CredentialRecord:
  id
  owner_scope: "global"
  kind: "oauth_token" | "api_key" | "manual_token"
  provider
  label
  redacted_summary
  expires_at?
  scopes
  secret_ref
  created_at
  updated_at
```

```text
CredentialSecret:
  credential_id
  encrypted_payload
  updated_at
```

Plaintext secret 不进入 bootstrap、prompt、history、runtime events。Admin write path 可接收明文，
read path 只返回 redacted view。

### AuthAttempt state machine

MCP / SaaS OAuth、device code、API key entry 都可以落到同一状态模型：

```text
create attempt
  -> waiting_for_user
  -> exchanging / polling
  -> credential_ready
  -> connection_ready
```

状态：

```text
AuthAttempt:
  id
  connection_id
  method: "oauth_pkce" | "device_code" | "api_key" | "manual"
  status: "waiting_for_user" | "polling" | "exchanging" | "succeeded" | "failed" | "expired"
  action
  error?
  expires_at?
```

Coding CLI login 不使用 AuthAttempt；它使用 Admin PTY + Integration probe。

## Admin UX

Admin UI 顶层资源建议：

```text
Connections
  MCP Servers
  Coding CLIs
  Native Integrations
  Credentials
  Skills
Terminal
```

MCP server detail：

- endpoint URL 或 stdio command/args、transport、auth mode
- status: disabled / disconnected / needs_auth / ready / degraded / error
- discovered tools/resources/prompts counts
- connect / reconnect / refresh metadata / login / reauth / disable / delete
- lifecycle、tool filter、output guard
- last error、last checked time、cache updated time

Coding CLI detail：

- binary detected / missing
- login readiness
- suggested login command
- Open Terminal
- Recheck

Credential detail：

- provider, label, scopes, expiry
- redacted summary
- revoke / refresh where supported

Terminal：

- ordinary server-side PTY
- AdminAuth only
- no LLM access

## Invariants

1. LLM never receives raw credentials, token payloads, config paths, or daemon secret material.
2. yuubot does not reimplement MCP JSON-RPC, transport, session, or schema validation; SDK owns protocol mechanics.
3. MCP remote tool schemas are not injected into initial LLM tool specs by default.
4. `search()` returns capability summaries only; `get_spec()` returns compressed Python-facing signatures.
5. Tool filters apply consistently to search/list/get_spec/invoke.
6. Oversized MCP output is guarded before it reaches prompt/history.
7. Coding CLI login is solved by Admin PTY and official CLI login flows, not by yuubot-specific credential extraction.
8. Coding CLI readiness is determined by adapter probe; file watch is optional.
9. Skills are behavior instructions and loaded progressively.
10. v1 scope is global personal hub; records may include `scope="global"` for future actor/workspace/user scope.
