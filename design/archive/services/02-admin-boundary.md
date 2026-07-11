> **已过时**：这是历史服务设计，仅供追溯，不得作为当前实现依据。当前权威设计见
> [`design/system-design.md`](../../system-design.md)。

# Design: Admin / Public Boundary and AdminAuth

**实现顺序：2**（依赖 [01-runtime-events.md](01-runtime-events.md)）

## Scenario

yuubot 在同一 ASGI 进程内同时服务管理面与公网能力，但对外暴露为两个 URL base。实现第一步
是把路由、认证与 handler 按边界拆开，再在其上叠加 inbound、share 等特性。

## Concepts

```text
admin_url_base   = 管理面根 URL；其下业务 handler 须 AdminAuth（loopback 例外见下）
public_url_base  = 公网根 URL；不经 AdminAuth；仅显式列出的静态与 webhook 路由
AdminAuth        = 管理面统一认证；public 边界不使用
AuthContext      = 认证成功后附加到 request 的身份与权限视图
PublicBoundary   = 不经 AdminAuth 的 HTTP 子集
AdminBoundary    = admin_url_base 下全部 HTTP / WebSocket
```

磁盘布局与反代示意见 [deployment-design.md](../deployment/deployment-design.md)。

## DeploymentConfig

```text
DeploymentConfig = {
  server: { host, port },
  admin_url_base: string,
  public_url_base: string,
  trusted_proxies?: string[],
  admin_auth: {
    mode: "proxy" | "builtin" | "loopback_bypass",
    builtin?: { session_cookie_name, csrf_header },
    proxy?: { user_header, groups_header? },
  },
}
```

本地开发可将两 base 设为同一 origin；`loopback_bypass` 时本机请求免 AdminAuth。

## HTTP error envelope

管理面与公网 JSON 响应共用同一错误形状（Share/KV/Inbound 各节引用此表，不重复定义）：

```json
{
  "error": {
    "code": "bad_request",
    "message": "human readable",
    "detail": {}
  }
}
```

| Status | code | 含义 |
| --- | --- | --- |
| 400 | `bad_request` | path、query、body 不合法 |
| 401 | `unauthorized` | 缺少或无效认证 |
| 403 | `forbidden` | 已认证但无权限（v1 管理面少见） |
| 404 | `not_found` | 资源不存在；public 上未白名单路径亦 404 |
| 409 | `conflict` | 状态冲突（KV etag、并发写等） |
| 422 | `configuration_required` | Integration/Actor 配置缺失 |
| 500 | `internal_error` | 未预期服务器错误 |
| 503 | `provider_unavailable` | 外部依赖不可用 |

成功响应返回资源 JSON 本身或小包装对象；HTTP 不承载 LLM token stream。

## AdminAuth

不变量：**到达 `admin_url_base` 业务 handler 前必须通过 AdminAuth**（loopback 例外与
`POST /api/admin/*` 额外 loopback gate 见下）。

### AuthContext

认证 middleware 成功后设置：

```py
class AuthContext(msgspec.Struct, frozen=True):
  user_id: str
  display_name: str | None = None
  groups: tuple[str, ...] = ()
  auth_method: Literal["proxy", "builtin_session", "loopback_bypass"]
```

Handler 从 `request.state.auth: AuthContext` 读取。v1 不区分细粒度 RBAC；能到达 admin
handler 即视为管理员。

### mode 行为

| mode | 行为 |
| --- | --- |
| `proxy` | 见 [Proxy 信任规则](#proxy-信任规则) |
| `builtin` | 见 [Builtin session / CSRF](#builtin-session--csrf) |
| `loopback_bypass` | 仅开发；client 为 loopback 时跳过认证并设 `auth_method=loopback_bypass` |

认证失败（非 loopback 例外）：

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json

{"error":{"code":"unauthorized","message":"authentication required"}}
```

### Proxy 信任规则

仅当 `request.client.host` 属于 `DeploymentConfig.trusted_proxies`（或直连 loopback 反代）
时解析 forwarded 头：

| Header | 用途 |
| --- | --- |
| `X-Forwarded-For` | 取**最左**可信跳之后的 client IP（用于 loopback gate 判定） |
| `X-Forwarded-User` | 必填；映射 `AuthContext.user_id` |
| `X-Forwarded-Groups` | 可选；逗号分隔 → `groups` |

非可信来源的连接：**忽略** forwarded 头，按 TCP 对端 IP 处理。反代须在 admin 虚拟主机
未认证时返回 401、不转发到 yuubot。

### Builtin session / CSRF

| 项 | v1 约定 |
| --- | --- |
| 登录 | `POST /api/auth/login`（实现细节可后补）；成功设 `HttpOnly` `Secure` `SameSite=Lax` session cookie |
| Session 校验 | 每个 admin HTTP 请求校验 cookie → `AuthContext` |
| CSRF | 状态变更（`POST`/`PUT`/`DELETE`）要求 header `X-CSRF-Token` 与 session 绑定 token 一致；`GET`/`HEAD` 豁免 |
| 登出 | `POST /api/auth/logout` 清除 session |

未登录或 CSRF 失败 → `401 unauthorized`（登录前）或 `403 forbidden`（已登录 CSRF 失败）。

### Loopback 例外

- `admin_auth.mode == loopback_bypass`：client 为 `127.0.0.1` / `::1` 时跳过 AdminAuth。
- `POST /api/actors/{actor_id}/inbound`：同机 loopback 默认放行（见 [03-inbound.md](03-inbound.md)）；远程须 AdminAuth 或 reverse SSH 回本机 loopback。
- `POST /api/admin/interrupt`、`POST /api/admin/shutdown`：**额外**要求 client 为 loopback，与 AdminAuth 并存。

## WebSocket contract

入口：`GET /api/ws`（仅 `admin_url_base`）。

### 认证

WebSocket **复用 HTTP AdminAuth middleware**：握手须在 upgrade 前完成认证（与同源 admin
HTTP 相同 cookie / proxy 头）。失败行为：

| 场景 | 行为 |
| --- | --- |
| 未认证 | HTTP `401` + 上述 JSON error；**不** upgrade |
| 认证后 upgrade | 正常 `101`；创建 `WsListener` 并 `listeners.add` |

v1 不在 WS 帧层做二次 token 交换；依赖反代与 cookie 在 upgrade 前完成身份。

### 帧形状

所有 frame 为 JSON object。

客户端命令：

```json
{ "id": "client-msg-id", "type": "conversation.send", "payload": {} }
```

服务端 ack / push：

```json
{ "id": "client-msg-id", "type": "conversation.stream", "payload": {} }
```

错误 frame：

```json
{
  "id": "client-msg-id",
  "type": "error",
  "error": { "code": "bad_request", "message": "...", "detail": {} }
}
```

未知 command `type` → `error` frame，`code=bad_request`。连接级协议错误可 `close(1008,
policy violation)` 并记录日志。

### 帧顺序（v1）

| 命令 | ack | push | 终端 |
| --- | --- | --- | --- |
| `conversation.send` | `conversation.send.accepted` | `conversation.stream` × N | `stream_stop` 或 `error` |
| `conversation.interrupt` | `conversation.interrupt.result` | — | 单帧 |
| `conversation.history.subscribe` | `conversation.history.subscribe.result` | `conversation.history.append` × N | 断开 |
| `runtime.events.subscribe` | `runtime.events.subscribe.result` | `runtime.event` × N | 断开 |
| `task.subscribe` | `task.subscribe.result` | `task.event` × N | task 终态或断开 |
| `task.cancel` | `task.cancel.result` | — | 单帧 |

规则：`*.accepted` / `*.result` 必须先于同 command 的 push stream。`conversation_busy` 仅
出现在 WS `error` frame，不是 HTTP status。

断开：`listeners.remove(ws_listener)`，取消连接内局部 task。

## Route Classification

### Admin boundary（`admin_url_base` + AdminAuth）

```text
GET  /healthz
GET  /api/bootstrap
/api/*                          # 配置、conversation、文件、runtime、tasks 快照等
GET  /api/ws                    # 对话与 runtime 订阅
POST /api/actors/{actor_id}/inbound
POST /api/shares ...
/api/actors/{actor_id}/kv/...
POST /api/admin/interrupt
POST /api/admin/shutdown
```

### Public boundary（`public_url_base`，无 AdminAuth）

```text
GET  /s/{share_id}/{path}
POST /webhooks/app/{integration_type}
```

公网 **不得** 出现 `/api/*`、KV、WebSocket、Actor inbound。

## ASGI Facade Shape

```text
AsgiApp
  ├─ mount_by_host(admin_url_base)  -> AdminRouter   # AdminAuth middleware 在内层
  └─ mount_by_host(public_url_base) -> PublicRouter  # 无 AdminAuth
```

实现检查清单：

1. `DeploymentConfig` 读取 URL base 与 `admin_auth`。
2. Admin router 挂 AdminAuth；public router 不挂。
3. 未列入 public 白名单的路径在 public host 上返回 404。
4. WS upgrade 走 AdminAuth；失败 401 JSON，不 upgrade。

## Context Access

```text
Core needs:
  admin_url_base, public_url_base, admin_auth, trusted_proxies
  client host / forwarded headers, AuthContext

Source:
  DeploymentConfig  <- process startup
  request.state.auth <- AdminAuth middleware

Access path:
  admin HTTP/WS -> AdminAuth -> handler
  public handler -> no AdminAuth

Missing context: none for boundary layer
Accepted debt:
  builtin 登录 UI 与 `/api/auth/*` 端点可实现为薄包装；contract 层要求 session+CSRF 语义。
  v1 管理面无 per-resource RBAC。
```

## Invariants

1. 管理能力与 durable 变更 API 只在 `admin_url_base`。
2. `public_url_base` 路由表为显式白名单。
3. AdminAuth 在 facade 层统一执行；handler 从 `AuthContext` 读取身份，不自行解析 cookie。
4. WebSocket 与 HTTP 共享 admin 边界与同一 AdminAuth 结果。
5. Secret 明文不出现在 bootstrap / snapshot。
6. Share 创建、KV 读写、配置 mutation 均属 admin 边界。

## Related

- 前置：[01-runtime-events.md](01-runtime-events.md)
- 下一实现：[03-inbound.md](03-inbound.md)
- 部署与 URL 公式：[deployment-design.md](../deployment/deployment-design.md)
- Share / KV wire：[05-share.md](05-share.md)、[06-kv.md](06-kv.md)
