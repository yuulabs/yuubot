# 10. 重构落地计划

v2 是专门重构分支，按 breaking rewrite 推进。原则是先建立清晰的新模型，再把旧入口删除或改到新模型上；不为了少量现有数据维护长期兼容层。

落地顺序强调：可读、可审查、类型明确、扩展点统一。每个阶段都应该能用较小 diff 审查，不把“顺手兼容旧行为”混进核心架构。

## Phase 0: 架构边界和删除清单

- 明确 trace 两端口：collector/ui。
- 在当前文档中停止使用"去掉 8782"这种表述，改为"不对公网暴露 UI port"。
- 明确 Bootstrap Config vs Runtime Resources。
- 明确 Character vs Actor。
- 明确 Web Chat DB commit ack。
- 删除 "Provider" 统一术语：LLM Backend（`llm_backends` 表）是 infra 配置；Integration（`integrations` 表）是插件扩展。两者不共享抽象层。
- 明确 Integration 是外部连接的唯一扩展模型。IM 平台（Discord、QQ、Telegram）也是 Integration。
- 明确 Integration 三层结构：Loader（代码级）、运行实例（DB 级，`factory.create()` 创建即激活）、`IntegrationIngress`（运行时入口）。Integration 通过 `gateway.open_integration(integration_id)` 获取 ingress；不存在独立 Channel 一等对象。
- 明确路由模型：`actor_ingress_rules` 表按 fnmatch glob 匹配 `MessageSource(id, path)` + `kind`；不维护 `channels` / `channel_targets` / `route_rules` 表。
- 明确 capability schema 用 `msgspec.Struct`：`CapabilitySpec.input_type` / `output_type`；invoke 链全程传 `dict[str, object]`，不引入 Pydantic 等第二套类型库。
- 明确 yuuagents 是 daemon infrastructure：配置在 Bootstrap Config 中，修改后重启 daemon；Actor DB 字段直接贴近 `AgentDefinition`。
- 明确 Web 内置入口支持多个 dialog，通过 `MessageSource.path = "dialog:<uuid>"` 区分，以便在没有外部 Integration 时测试 ingress rule 分流。
- 列出现有旧路径删除清单：`llm.yaml` 运行时读取、`agent_llm_refs` 作为长期接口、手写 config override、散落的 integration-specific patch、QQ / NapCat / OneBot core adapter、ChannelAdapter 作为独立扩展点。
- 删除 `ChannelAdapter` / `ChannelAdapterFactory` / `ChannelAdapterSupervisor` 作为核心扩展点。Integration 是外部连接的唯一扩展模型。

## Phase 1: Typed Resource Registry 基础

新增 DB 表和 typed model，不先做兼容实现：

- `secrets`
- `llm_backends`
- `integrations`
- `prompt_templates`
- `characters`
- `actors`
- `actor_ingress_rules`

实现统一 ResourceRepository：

```text
parse typed request
  -> validate schema and references
  -> write DB in transaction
  -> bump version
  -> publish ResourceChanged on EventBus
  -> subscribers (RouteBindingService / ActorManager / IntegrationCore) reconcile
```

要求：

- 每个 Resource 有 msgspec record、Tortoise ORM 映射、validator 和最小 E2E 测试。
- JSON 字段必须有 msgspec schema，不以裸 `dict[str, Any]` 作为跨模块契约。
- Runtime Registry 只从 DB hydrate，不从旧 YAML 混合读取。
- `IntegrationFactoryRegistry` 只注册 Integration Factory code，不保存 Runtime Resource instance。LLM Backend 不需要 plugin registry。

## Phase 2: LLM Backend / Integration UI

- Admin UI 支持创建 LLM backend。
- 支持内置模板；模板由 setup/assembly 作为普通初始化输入传入，例如 `characters=[...]`、`actors=[...]`，不建立专门 builtin registry。
- 支持 OpenAI-compatible。
- 支持 secret 加密。
- 支持 test connection。
- 建立 Integration / Capability 页面骨架，为 GitHub、Linear、W&B、SwanLab、搜索服务等三方接入留统一入口。
- 如果需要保留旧 key，提供显式 `import-legacy` 工具导入 backend；导入不是启动流程的一部分。

## Phase 3: Character / Actor UI

- Character 页面：clone builtin、section editor、reset builtin。
- Actor 页面：选择 character、backend/model、runtime policy、resource policy。
- Actor table 成为模型绑定唯一事实来源。
- Actor table 保存 `AgentDefinition` 形状字段：`llm_options`、`budget`、`agent_capabilities`、`agent_prompt_providers`、`allowed_capability_ids`。
- yuuagents `StageConfig.providers` 不放入 Runtime Resources 热更新路径；改配置后重启 daemon。
- 删除 `agent_llm_refs.<character>` 作为运行时接口；需要保留的值通过一次性 import 写入 Actor model binding。
- Actor Runtime 只机械拼装 Actor + LLM Backend + Character + BootstrapConfig.yuuagents，不再读 legacy config 或维护第二套 Actor DSL。

## Phase 4: Ingress / Integration Lifecycle

- Admin UI 支持 ActorIngressRule CRUD：选 actor + 输入 source_id/source_path/kind glob。
- 内置 Web 入口 integration：用 `integration_id = "web-admin"` 标识，每个 dialog 用独立 `source.path`。
- Web dialog CRUD：创建/重命名/归档 dialog；每个 dialog 用独立 `MessageSource.path`，可通过 ingress rule 路由到不同 actor 验证投递。
- Integration lifecycle：`IntegrationCore.enable / disable / reconcile`，幂等 `factory.create()`。
- Integration 启用时通过 `gateway.open_integration()` 获取 ingress；停用时 instance.close() 自然失效。
- 删除 integration 不级联删除 ingress rule；admin UI 检测悬空 source_id_pattern 并提示。
- QQ / NapCat / OneBot 作为 Integration 接入，不作为 core ChannelAdapter。
- Optional Discord Integration 作为 contract 示例。
- Integration 只输出标准 `IncomingMessage`，不把平台 raw event 传入下游。

## Phase 5: Web Chat Queue

- Admin `/ws/chat` 写 DB 队列。
- DB commit 后 ack。
- daemon 消费 pending queue → web integration ingress.emit() → Gateway.ingest()。
- crash recovery：重试 pending/expired processing。
- Web Chat 走标准 Integration + ActorIngressRule 路径，不保留单独 conversation 特例路径。

## Phase 6: Trace Proxy Fix

- Bootstrap Config 增加 `trace.collector_port` 和 `trace.ui_port`。
- Admin `/monitor/` 代理到 UI port。
- UI port 不直接公网暴露。

## Phase 7: Bridge

- registration token。
- client verifies server。
- tunnel key / command key 分离。
- node registry。
- heartbeat。
- `yb.bridge_*` master-only API。
- Bridge 暴露为 Infrastructure / Resource capability，不作为 Integration，也不绕过 Actor resource policy。

## Phase 8: Schema Migrations

v2 可以破坏旧数据，但仍需要正式迁移机制来支撑之后的重构：

- 每次 schema change 有 migration。
- migration 可重复执行。
- Docker entrypoint 启动前运行 pending migrations。
- 旧配置 import 脚本与 DB migration 分离：migration 维护 schema，import 只做用户选择的数据搬运。

## 初始 P0/P1 列表

P0：

- trace 两端口配置与 monitor proxy 文档/实现对齐。
- `admin.secret` 非空检查和 localhost 限制。
- 定义并落地 ResourceRepository / EventBus 最小骨架。
- 删除或隔离旧 LLM config 运行时读取路径。
- 移除 QQ / NapCat / OneBot core adapter 及其专用维护路径。
- 移除 Pydantic 依赖，capability schema 统一到 msgspec。

P1：

- LLM Backend DB + Integration UI skeleton。
- Secret store。
- Actor model binding 从 DB 读取。
- yuuagents actor startup assembly：从 DB resource 构造 `Stage` / `AgentDefinition` / `Actor`，但不做热更新。
- Web dialog history load + DB commit ack。
- Runtime resource reload service（基于 ResourceChanged 事件）。
- Integration capability manifest 草案（msgspec input/output）。

P2：

- Character editor。
- ActorIngressRule UI。
- Web integration connect flow。
- Trace deployment cleanup。
- `import-legacy` 可选工具。

P3：

- Bridge Gateway-facing resource capability。
- Bridge client。
- DB migration framework hardening。
