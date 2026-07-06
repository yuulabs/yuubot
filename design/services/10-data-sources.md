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
- Coding CLI隶属于Integration范围， 使用原生登录流；Admin UI 提供服务器侧 PTY 解决 headless 登录（没有无头登陆的coding cli直接让它爬）。
- Skill 是按需加载的 SOP / 规范 / 工作流知识，不是 data source。

参考：

- MCP introduction: <https://modelcontextprotocol.io/docs/getting-started/intro>
- MCP remote server connection: <https://modelcontextprotocol.io/docs/develop/connect-remote-servers>
- MCP authorization: <https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization>
- Agent Skills: <https://agentskills.io/home>

## Concepts

```text
MCP Server       = 外部能力端点；remote HTTP 为主，stdio 可后补
Connection       = daemon 对某个外部能力端点的配置与运行态
Credential       = daemon 加密托管的 OAuth token / API key / refresh token 等
Integration      = yuubot 内部增强能力；负责本地/产品特定 readiness probe 与 facade
Coding CLI       = Codex / Opencode / Cursor / Claude Code 等官方 CLI
Admin PTY        = 管理员在浏览器中操作服务器侧终端，用于原生 CLI 登录与诊断
Skill            = SOP / 规范 / 行动指南；默认只暴露 name / description
Capability Index = daemon 缓存的 MCP tools/resources/prompts 轻量目录
```

不要把三类能力混成同一抽象：

| 类型 | 主要职责 | 登录归属 | LLM 调用面 |
| --- | --- | --- | --- |
| MCP | 标准 data source / tool / resource 总线 | daemon Credential | `yb.mcps` |
| Coding CLI Integration | 本地编码 agent readiness 与任务入口 | 官方 CLI 写入系统用户 HOME | `yb.coding` / integration facade |
| Skill | 行为规范、SOP、领域流程 | 无 runtime credential | prompt 按需展开 |

## MCP

MCP 是 data source 主路径。daemon 是 MCP client；yuubot v1 不把自己暴露为 MCP server。

### Supported transports

v1 主路径：

```text
remote HTTP MCP
  -> daemon owns auth
  -> daemon performs initialize / discovery / call
  -> Python facade uses daemon local RPC
```

暂不考虑local

### Remote MCP auth

Remote HTTP MCP 的标准路径：

```text
Admin adds MCP server URL
  -> daemon probes MCP endpoint
    -> if public: initialize and discover capabilities
    -> if auth required:
         discover protected resource metadata
         discover authorization server metadata
         create OAuth attempt with PKCE and resource parameter
         return Admin action
  -> Admin opens browser auth URL
    -> callback returns to yuubot public/admin URL
      -> daemon exchanges code, stores encrypted token
        -> daemon reconnects and refreshes capability index
```

Auth modes:

| Mode | 用途 |
| --- | --- |
| `none` | public MCP server |
| `api_key` | 非标准或 vendor-specific bearer/header key |
| `oauth_auto` | MCP spec discovery + OAuth 2.1 / PKCE |
| `oauth_manual` | Admin 手填 auth/token endpoint、client metadata、scope 等 escape hatch |

Client identity 默认使用 yuubot 部署 URL 暴露 metadata / callback。若 server 不支持 metadata
或 discovery，可 fallback 到 dynamic registration 或 manual client 配置。

### Records and state

Durable record 存 metadata，不存明文 secret：

```text
McpServerRecord:
  id
  name
  endpoint_url
  transport: "http" | "stdio"
  auth_mode: "auto" | "none" | "oauth" | "api_key"
  credential_id?
  enabled
  created_at
  updated_at
```

Runtime state 由 daemon probe / session manager 维护：

```text
McpServerState:
  status: "disabled" | "checking" | "needs_auth" | "ready" | "degraded" | "error"
  capabilities_summary
  last_error
  action_hint?
  last_checked_at
```

Credential secret payload 经 encrypted store 读写，Admin/bootstrap 只返回 redacted summary、
expiry、scope、configured flag、last_error。

### Capability discovery

daemon 在连接 ready 后获取并缓存轻量 index：

```text
server
  tools: name, description
  resources: uri/name, description, mime-ish hints
  prompts: name, description
```

`search()` 只返回摘要，不返回参数说明。调用方需要具体参数时再 `get_spec()`。

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
  list_tools() -> list[McpCapabilitySummary]
  list_resources() -> list[McpCapabilitySummary]
  list_prompts() -> list[McpCapabilitySummary]
  get_spec(name: str) -> str
  invoke(name: str, **kwargs) -> McpResult
  read_resource(uri: str) -> McpResult
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

新增专门的MCP页面

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

- endpoint URL / transport / auth mode
- status: ready / needs_auth / degraded / error
- discovered tools/resources/prompts counts
- connect / reauth / refresh / disable
- last error and last checked time

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
2. MCP remote tool schemas are not injected into initial LLM tool specs.
3. `search()` returns capability summaries only; `get_spec()` returns compressed Python-facing signatures.
4. Coding CLI login is solved by Admin PTY and official CLI login flows, not by yuubot-specific credential extraction.
5. Coding CLI readiness is determined by adapter probe; file watch is optional.
6. Skills are behavior instructions and loaded progressively.
7. v1 scope is global personal hub; records may include `scope="global"` for future actor/workspace/user scope.

