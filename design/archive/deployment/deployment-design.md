> **已过时**：这是历史部署设计，仅供追溯，不得作为当前实现依据。当前权威设计见
> [`design/system-design.md`](../../system-design.md)。

# Design: Deployment Boundaries

## Scenario

yuubot 监听本机端口；Caddy/Nginx 按 Host 暴露两个公网入口。管理员操作与公网静态浏览使用不同 URL base 与认证策略。

## Concepts

```text
admin_url_base   = 管理面根 URL；其下全部 HTTP/WS 须 AdminAuth
public_url_base  = 公网根 URL；不经 AdminAuth；静态 Share 与 app webhook
AdminAuth        = 管理面统一认证；public_url_base 不使用
published/       = 公网静态文件磁盘根（与 workspace 分离）
```

## DeploymentConfig

```text
DeploymentConfig = {
  server: { host: "127.0.0.1", port: 8765 },
  admin_url_base: "https://admin.example.com",
  public_url_base: "https://public.example.com",
  trusted_proxies: [...],
  admin_auth: { mode: "proxy" | "builtin" | "loopback_bypass" },
}
```

本地开发可将两个 base 设为同一 origin；`loopback_bypass` 时免 AdminAuth。

## AdminAuth

不变量：**到达 `admin_url_base` 业务 handler 前必须通过 AdminAuth。**

| mode | 行为 |
| --- | --- |
| `proxy` | 反代已认证；yuubot 信 `trusted_proxies` 与 `X-Forwarded-User` 等 |
| `builtin` | yuubot 校验 `Authorization` / session |
| `loopback_bypass` | 仅本地开发 |

反代对 admin 虚拟主机未认证则 401、不转发。yuubot 无身份则 `401 unauthorized`。

## URL bases 与路由归属

| base | 路由 | 认证 |
| --- | --- | --- |
| `admin_url_base` | 现有 `/api/*`、`/api/ws`、`POST /api/actors/{actor_id}/inbound`、§ 各服务管理端点 | AdminAuth；inbound 同机 loopback 默认放行 |
| `public_url_base` | `GET /s/{share_id}/{path}`、`POST /webhooks/app/{integration_type}` | share id / adapter auth |

`public_url_base` 上不得出现 `/api/*`、KV、WebSocket。反代宜对 public 主机 `respond /api/* 404`。

URL 拼装（唯一定义处）：

```text
share_url        = public_url_base + "/s/" + share_id + "/" + rel_path
actor_inbound_url = admin_url_base + "/api/actors/" + actor_id + "/inbound"
preview_url      = admin_url_base + "/api/actors/" + actor_id + "/files/" + path
kv_url           = admin_url_base + "/api/actors/" + actor_id + "/kv/" + key
```

## Data paths

```text
data_dir/
  workspace/{actor_id}/...     # bot 工作区；不公网直出
  published/{share_id}/...     # Share 快照；见 services/05-share.md
  kv/{actor_id}/{key}.json     # JSON KV；见 services/06-kv.md
  db/
  logs/
```

路径解析须 containment；拒绝 `..` 与符号链接逃逸。

## Pre-deployment Gate: Logging and Resource Management

部署前必须先补齐 log 与资源管理；这类能力是长期云服务的运行前提，不应推迟到部署后补救。

**Logging**：yuubot 需要在关键 runtime 路径适当增加结构化 log，目标是支持线上 debug。log 与 trace 正交：
trace 用于分析链路、耗时、成本和 profiling；log 用于解释 runtime 行为、错误现场、恢复路径和关键状态转换。

**Disk management**：yuubot 预期作为云上长期服务运行，必须主动管理磁盘：

- `/tmp` 定时清理；LLM 与工具调用很容易把临时产物写到这里。
- `data_dir/logs/` 需要轮换与保留策略。
- `app_spans`、trace artifacts、未来 traces 数据也需要轮换；这是长期目标，但部署前至少要有明确策略和可执行的第一阶段。
- 磁盘空间不足时触发 runtime 告警，不能只依赖宿主机报错。

**Runtime monitoring**：Runtime 需要暴露传统服务器信息，用于 Admin/Monitor 和告警判断：

- CPU 占用率。
- 内存占用率。
- 磁盘使用率与剩余空间。
- 网络相关指标（吞吐、连接或错误，按实现阶段细化）。

**Memory and concurrency limits**：所有可能造成大内存占用的并发都需要被管理，典型例子是 `execute_python`。实现上应明确队列、并发上限、超时、取消、输出/产物大小限制，以及达到限制时的用户可见错误。

## Reverse proxy（示意）

```text
admin.example.com  { forward_auth … ; reverse_proxy 127.0.0.1:8765 }
public.example.com { respond /api/* 404 ; reverse_proxy 127.0.0.1:8765 }
```

## Actor inbound 与 reverse SSH

远程可信主机（bot 已能 SSH 登录）向 actor 投递消息时，不暴露公网 actor 端点；在远程建立
reverse SSH，使回调经隧道从 yuubot 本机 loopback 进入 `actor_inbound_url`。v1 以 SSH
信任边界为主；请求级验证协议后续补充。

## Invariants

1. 管理能力与储存 API 只在 `admin_url_base`。
2. 公网静态只读 `published/{share_id}/`，不映射 workspace。
3. 公网 inbound 仅 app webhook；actor 入站不在 `public_url_base`。
4. Share 创建权在人类管理员，不在 Actor（见 services/05-share.md）。

## Related

- 实现顺序索引：[services/README.md](../services/README.md)
- Runtime 事件与 listener：[01-runtime-events.md](../services/01-runtime-events.md)
- Admin 边界：[02-admin-boundary.md](../services/02-admin-boundary.md)
- Inbound：[03-inbound.md](../services/03-inbound.md)
- Tasks / `yb.tasks`：[04-tasks.md](../services/04-tasks.md)
- Share：[05-share.md](../services/05-share.md)
- KV：[06-kv.md](../services/06-kv.md)
