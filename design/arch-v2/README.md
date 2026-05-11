# yuubot Architecture v2

> 状态：草案 v2  
> 日期：2026-05-03  
> 目标：把 yuubot 从“配置很多的 bot 程序”重构为“以 Runtime Resources 为核心的多渠道 Agent 平台”。这是 breaking rewrite，不把现有 YAML 形状、DB 形状或临时 workaround 作为设计约束。

## 核心结论

v2 的关键变化是：**不要再把可运行系统理解成一组 YAML key，而是把用户可管理的对象建模为 LLM Backend / Integration / Character / Actor / Channel。**

- **Bootstrap Config**：只负责让系统启动，例如 DB 路径、admin 端口、trace 两个端口、secret master key。修改通常需要重启。
- **Runtime Resources**：用户在 Admin UI 中创建和修改，存 DB，支持在线变更，包括 LLM Backend、Integration、Character、Actor、ActorIngressRule。
- **yuuagents 是 daemon 基建**：`StageConfig.providers` 等 executor wiring 从 Bootstrap Config 加载，修改后重启 daemon；Actor DB 字段直接贴近 `AgentDefinition`，避免再造一套 yuubot agent DSL。
- **Character 是模板，Actor 是运行实例**：同一个 Character 可以对应多个 Actor，每个 Actor 有自己的模型绑定、资源策略、memory/rollover/tool 权限。
- **Integration 是外部连接的唯一扩展点**：所有外部连接统一走 Integration 模型。IM 平台（Discord、QQ、Telegram）也是 Integration。Integration 通过 `gateway.open_integration(integration_id)` 拿到 `IntegrationIngress`，把外部消息盖戳为 `MessageSource(producer="integration", id=integration_id, path=...)` 后投入 Gateway。
- **平台没有 Channel 一等对象**：消息路由用 `ActorIngressRule` 匹配 `MessageSource`。"Channel" 只是 Admin UI 中按 integration / source path 分组的视觉概念，不对应 DB 表也不进入运行时数据流。
- **路由是 source pattern 匹配**：`ActorIngressRule` 用 fnmatch glob 匹配 `(source.id, source.path, kind)`，命中则把消息投到对应 actor mailbox。每个 enabled actor 自动得到一条 `system:<actor_id>` 规则用于 actor 间消息。不设 priority、route_rules 多表 join、Context pinning。
- **平台不持有 IM 概念**：Context、Session、Thread 等概念不属于平台抽象。Integration 可以内部管理会话隔离，GroupActor 可以按 group_id 做 per-context execution state，但平台 Gateway 不知道这些；它只看 `MessageSource`。
- **Integration 有生命周期**：Loader（代码级注册）→ 运行实例（DB 持久化，`factory.create()` 创建即激活）→ `IntegrationIngress`（运行时入口）。启用时通过 `Resources` / `ResourceRepository` 读写 DB，停用时释放 ingress 但 IntegrationConfig 保留。
- **Web Channel 要能验证投递机制**：Web 不只是一个固定 admin chat，而应支持多个对话框（独立 source_path），用来测试 ingress rule 是否按预期把消息路由到 actor。
- **LLM Backend 与 Integration 完全分离**：
  - LLM Backend 是 infra 配置（`llm_backends` 表），概念上属于 daemon infrastructure，只是通过 Admin UI 热更新。新增 LLM 厂商走 yuuagents 后端 contract。
  - Integration 是外部连接和能力扩展（`integrations` 表），通过 capability manifest 和 plugin registry 接入。新增搜索、SaaS 或内部服务走 capability-based contract。
  - 两者不共享抽象层，没有统一的 "Provider" umbrella。
- **Capability schema 用 msgspec**：`CapabilitySpec.input_type` / `output_type` 是 `msgspec.Struct`。框架只在边界做 dict ↔ Struct 的 validation，invoke 调用链全程传 dict，不引入第二套类型库（如 Pydantic）。
- **Web Chat 的可靠性边界是 DB commit**：admin 收到消息并落盘成功即 ack，daemon 异步消费。
- **Trace 明确两个端口**：collector port 用于写入 trace；ui port 用于浏览器查看，Admin `/monitor/` 代理到 UI port。
- **Bridge 初版安全原则**：client 必须严格验证 server；private key 不离开所属机器；注册 token 一次性；tunnel key 与 command key 分离。

## 重构原则

v2 的优先级是长期可维护性，而不是兼容旧实现。

- **可读性优先**：领域对象、边界和数据流要能被 reviewer 顺着读完，不靠隐式全局状态或历史配置约定。
- **架构完整但核心很小**：核心框架只包含稳定抽象、Web Channel、资源管理、Actor Runtime、观测和安全边界；非核心 adapter 不进入主路径。
- **类型明确**：跨模块边界使用 typed model / protocol / schema；DB JSON 字段也要有明确 schema 和 validation，不把 `dict[str, Any]` 当长期接口。
- **扩展点边界统一**：三方服务和新渠道按 Integration / Capability 模型接入；新增能力应主要新增 plugin 和 capability manifest，而不是修改多条业务路径。IM 平台也是 Integration，只是恰好只提供 Channel。
- **简单可审查**：宁可接受 breaking change、移除非核心 adapter 和一次性数据导入，也不要维护双路径兼容、隐式 fallback 或长尾迁移分支。

## 文档目录

1. [系统分层与术语](./01-system-model.md)
2. [Bootstrap Config](./02-bootstrap-config.md)
3. [Runtime Resources](./03-runtime-resources.md)
4. [Gateway / Channel / Route](./04-gateway-routing.md)
5. [Admin 用户流程](./05-admin-ux.md)
6. [配置与热更新语义](./06-hot-reload.md)
7. [Trace 与 Observability](./07-trace.md)
8. [Bridge 节点与远程资源](./08-bridge.md)
9. [多进程、部署与安全](./09-deployment-security.md)
10. [重构落地计划](./10-migration-plan.md)
11. [API 设计与安全](./11-api-design.md)

## 非目标

- v2 不追求兼容旧 `llm.yaml` / `docker_config.yaml` 运行语义。必要时只提供一次性导入工具；导入完成后 DB Runtime Resources 是唯一事实来源。
- v2 不保留旧配置和新资源的双写、双读或隐式 fallback。发现旧路径时应删除、迁移或明确隔离为 import-only。
- v2 不要求所有配置都热更新。只有 Runtime Resources 和明确白名单的 runtime flags 支持在线修改。
- v2 不把 Bridge 作为外部连接。Bridge 是基础设施层，可被 Integration 或 Actor 使用。
- v2 core 不承诺内置 QQ / NapCat / OneBot；这些作为 Integration 接入，不进入核心框架。
- v2 不引入独立的 Channel 表 / ChannelAdapter 扩展点。Integration 是外部连接的唯一扩展模型，路由通过 `ActorIngressRule` 匹配 `MessageSource` 完成。
- v2 不在 capability invoke 链里使用第二套 schema 库（如 Pydantic）。Capability 输入输出类型一律用 `msgspec.Struct`，运行时传 `dict[str, object]`。
