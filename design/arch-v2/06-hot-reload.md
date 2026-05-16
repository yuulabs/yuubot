# 06. 配置与热更新语义

v2 需要明确每类修改什么时候生效，避免"看起来改了但其实没生效"。热更新设计也必须服务于可读性和审查：所有在线修改都走 typed resource 服务，不保留旧配置兼容路径。

## 三类可变性

### Static Bootstrap

来源：`.env` / `config.yaml`。

特征：

- 修改需要重启。
- UI 可读，默认不可在线写。
- 示例：DB path、admin port、trace ports、secret master key、yuuagents provider wiring。

### Runtime Resource

来源：DB domain tables。

特征：

- Admin UI 可创建、修改、禁用。
- 生效范围由 resource type 定义。
- 每类 resource 有 typed schema / validator / version。
- 示例：LLM Backend、Integration、Character、Actor、ActorIngressRule。

### Runtime Flag

来源：`AdminConfigKV` 或小型 settings 表。

特征：

- 少量全局策略。
- 必须有白名单和 schema。
- 示例：system default actor id、global response style、UI preference。

## Config Registry

建议引入配置注册表，让 UI 和后端共享每个 key 的元信息。

```python
ConfigField(
    key="trace.ui_port",
    source="bootstrap",
    value_type=int,
    hot_reload=False,
    restart_required=True,
    secret=False,
    description="Trace UI internal port used by admin monitor proxy.",
)

ConfigField(
    key="system.default_private_actor_id",
    source="runtime_flag",
    value_type=int,
    hot_reload=True,
    restart_required=False,
    secret=False,
    description="Fallback actor for private contexts.",
)
```

## yuuagents 修改语义

`yuuagents` 是 daemon 执行基建，不是 Runtime Resource Provider，不需要热更新。

```text
Update yuuagents StageConfig.providers / strict mode
  修改 config.yaml / bootstrap config。
  Admin UI 标记 restart required。
  用户点击 restart daemon 或手动重启。
  daemon 重启后重新构建 Stage/Actor/Agent。
```

禁止为 yuuagents executor config 做 shadow hot-reload path。Actor 可以在线修改自己保存的 `AgentDefinition` 字段，但这些修改只影响新启动/新 turn；已经创建的 yuuagents Actor/Agent 不强制在线 patch。

## LLM Backend 修改语义

```text
Create LLM backend
  立即可用于新 Actor。

Update api_key/base_url/models
  validate -> save secret/config -> bump version。
  新 turn 使用新 version。
  正在执行的 turn 不打断。

Disable LLM backend
  新 turn 不再使用。
  若 Actor 仍引用，Actor status = degraded / needs_provider。

Delete LLM backend
  如果被 Actor 引用，禁止删除，只能 disable。
```

## Integration 修改语义

```text
Create integration (创建并启用)
  validate -> save config to DB -> bump version
  -> IntegrationCore.enable(integration_id)
    -> factory.create(record, gateway=gateway, storage=storage): 创建即激活
      -> instance 通过 gateway.open_integration() 拿 IntegrationIngress
      -> 向外部服务注册
  -> 立即可用于新 turn 的 capability resolver 和消息路由

Update config
  新 turn 重新生成 yb facade manifest。
  正在执行的 tool call 不打断。
  如果需要重新协商外部注册（如 webhook URL 变更），需 disable + enable。

Disable integration (运行时停用)
  -> IntegrationCore.disable(integration_id)
    -> instance.close(): 释放所有资源
      -> 向外部服务注销
      -> 不再向 Gateway 投递消息（IntegrationIngress 自然失效）
  -> 新 turn 不再解析到该 provider。
  -> 旧 Python session 中残留的 yb 函数也必须在 daemon dispatcher 侧被拒绝。
  -> ActorIngressRule 保留（按 integration_id 命中的规则在 integration 重新启用后自动恢复路由）。

Enable integration (重新启用)
  -> IntegrationCore.enable(integration_id)
    -> factory.create(...) 幂等恢复
      -> 重新向外部服务确认/更新注册
  -> 新 turn 重新解析到该 provider。

Delete integration
  如果还在启用状态，先 disable。
  删除 IntegrationConfig。
  引用该 integration 的 ActorIngressRule（按 source_id_pattern 命中 integration_id）失效——admin UI 应提示管理员清理或修改这些 rule。
```

Integration 差异应由 Plugin 和 capability schema 表达。新增三方服务不能通过修改 Actor Runtime、Route Engine 或 prompt 装配逻辑来"顺手兼容"。

## Character 修改语义

```text
Create character
  立即可用于新 Actor。

Update system prompt/tool surface
  bump character.version。
  新 conversation 默认使用新版本。
  已 pin 的 active conversation 是否重载，由 Actor policy 决定。

Reset builtin
  恢复到当前 builtin version。
```

建议初期规则：Character 修改对新 turn 生效，但 active actor session 不强制中断；UI 提示"部分长会话可能在 rollover 后完全生效"。

## Actor 修改语义

```text
Update model binding
  新 turn 生效。

Update runtime policy
  新 turn 生效。

Update capability permissions
  新 turn 生效，正在执行的 tool call 不打断。
  修改的是 Actor.allowed_capability_ids 与 AgentDefinition-shaped capability config。

Enable / Disable actor
  触发 actors 表 ResourceChanged → RouteBindingService.reload() → ActorManager.reconcile()。
  Disable 后该 actor 的 mailbox 关闭，新消息不再投递；其 system rule 自动从 RouteBindings 中剔除。
```

## ActorIngressRule 修改语义

```text
Create / Update / Delete rule
  写 actor_ingress_rules 表 → ResourceChanged → RouteBindingService.reload()
  → 立即生效（替换 Gateway 的 RouteBindings 快照）
  正在 in-flight 的消息不受影响。

Disable rule (enabled=false)
  与 delete 等效，但保留行用于 UI / 审计；下次 reload 时不会进入快照。

System rule (source_id = "system:<actor_id>")
  无需也不可手动管理。actor enabled 时自动出现在快照中。
```

## 统一写路径

所有 runtime 修改必须走统一服务：

```text
parse typed request
  -> validate schema and references
  -> write DB in transaction
  -> bump version if needed
  -> patch in-memory registry
  -> notify affected component
  -> return applied/errors
```

禁止新的代码路径只改内存不写 DB。也禁止为了保留旧行为而增加 shadow config、隐式 fallback 或 integration-specific patch path；这些都会降低审查性并放大三方服务接入成本。
