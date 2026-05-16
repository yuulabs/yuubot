# 09. 多进程、部署与安全

用Docker Compose打包部署

## 进程分离

推荐将 daemon 和 admin 分离：

```text
yuubot-daemon
  - Gateway
  - Channel adapters or channel workers
  - Actor runtime
  - /agent-fns/*
  - /bridge/*
  - health endpoint

yuubot-admin
  - Admin UI static files
  - /ws/chat
  - /ws/terminal if enabled
  - /api/* resource management
  - /monitor/* trace UI proxy
```

共享资源：

- SQLite DB，WAL 模式。
- Logs。
- Trace service endpoints。
- Secret master key from env/config。

## Admin 与 daemon 通信

Admin -> daemon：

- health check。
- runtime resource reload notify。
- optional direct API for actor/channel status。

Web Chat：

- admin 写 DB 队列后 ack。
- daemon 异步消费。

## Threat Model

主要威胁：

1. 未授权访问 Admin 面板。
2. API key / OAuth token 泄露。
3. Terminal / file surfaces 被公网暴露。
4. Channel token 泄露。
5. Bridge 节点伪造或 server spoofing。
6. LLM budget / pricing 配置错误导致超支。

## Admin 安全

- `admin.secret` 必须非空。
- 若 `admin.secret` 为空，只允许绑定 `127.0.0.1`，并打印强警告。
- 部署在公网时必须经 HTTPS 反向代理。
- cookie 在 HTTPS 部署中设置 `secure=True`、`httpOnly=True`、`sameSite=Lax/Strict`。
- Terminal / file APIs 需要显式启用，默认仅 local/admin 可用。

## Config Secrets

- Provider key 和 OAuth token 不写 YAML，也不进入独立 secrets 表。
- Integration config 中用 `Secret` 类型声明敏感字段。
- DB 中只存 master-key 加密后的 secret 字段密文。
- Admin UI 默认不回显明文，只在用户主动 reveal 时请求明文。
- 导出/备份时明确提示 secret 依赖 `YUU_SECRET_KEY`。

## Budget Protection

- 如果 Actor 或 Provider 配置了 budget，但模型没有 pricing，拒绝执行或要求用户确认禁用 budget。
- Provider test connection 应返回模型能力与价格状态。
- Actor 页面显示预算风险。

## 部署拓扑

```text
Internet
  -> HTTPS :443
  -> Caddy / nginx
       /          -> yuubot-admin
       /monitor/  -> yuubot-admin -> trace.ui_port
       /bridge/   -> yuubot-daemon / bridge service
       /channels/ -> optional channel management endpoints

Internal network
  yuubot-admin
  yuubot-daemon
  trace collector/ui
  channel workers
```

原则：

- 对外只暴露统一 HTTPS 入口。
- trace collector/ui 只内部开放。
- daemon/admin 端口只绑定 localhost 或内部网络。
- Bridge registration endpoint 必须有 token 认证，并受 rate limit。
