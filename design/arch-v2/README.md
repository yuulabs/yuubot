# yuubot Architecture v2

> 状态：草案 v2  
> 日期：2026-05-03  
> 目标：把 yuubot 从“配置很多的 bot 程序”重构为“以 Runtime Resources 为核心的多渠道 Agent 平台”。

## 核心结论

v2 的关键变化是：**不要再把可运行系统理解成一组 YAML key，而是把用户可管理的对象建模为 Provider / Character / Actor / Channel / Route。**

- **Bootstrap Config**：只负责让系统启动，例如 DB 路径、admin 端口、trace 两个端口、secret master key。修改通常需要重启。
- **Runtime Resources**：用户在 Admin UI 中创建和修改，存 DB，支持在线变更，包括 LLM Provider、外部服务、Character、Actor、Channel、Route。
- **Character 是模板，Actor 是运行实例**：同一个 Character 可以对应多个 Actor，每个 Actor 有自己的模型绑定、资源策略、memory/rollover/tool 权限。
- **Channel 是外部入口，Route 决定消息交给哪个 Actor**：Gateway 不理解平台细节，只处理标准化消息和 Context。
- **Web Chat 的可靠性边界是 DB commit**：admin 收到消息并落盘成功即 ack，daemon 异步消费。
- **Trace 明确两个端口**：collector port 用于写入 trace；ui port 用于浏览器查看，Admin `/monitor/` 代理到 UI port。
- **Bridge 初版安全原则**：client 必须严格验证 server；private key 不离开所属机器；注册 token 一次性；tunnel key 与 command key 分离。

## 文档目录

1. [系统分层与术语](./01-system-model.md)
2. [Bootstrap Config](./02-bootstrap-config.md)
3. [Runtime Resources](./03-runtime-resources.md)
4. [Gateway / Channel / Context / Route](./04-gateway-routing.md)
5. [Admin 用户流程](./05-admin-ux.md)
6. [配置与热更新语义](./06-hot-reload.md)
7. [Trace 与 Observability](./07-trace.md)
8. [Bridge 节点与远程资源](./08-bridge.md)
9. [多进程、部署与安全](./09-deployment-security.md)
10. [迁移与实现计划](./10-migration-plan.md)

## 非目标

- v2 不要求一次性删除 `llm.yaml` / `docker_config.yaml`。短期可以继续兼容它们，作为 seed 或 migration source。
- v2 不要求所有配置都热更新。只有 Runtime Resources 和明确白名单的 runtime flags 支持在线修改。
- v2 不把 Bridge 作为 Channel。Bridge 是基础设施层，可被 Channel 或 Actor 使用。
