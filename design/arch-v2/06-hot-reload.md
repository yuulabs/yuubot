# 06. 配置与热更新语义

v2 需要明确每类修改什么时候生效，避免“看起来改了但其实没生效”。

## 三类可变性

### Static Bootstrap

来源：`.env` / `config.yaml` / optional legacy files。

特征：

- 修改需要重启。
- UI 可读，默认不可在线写。
- 示例：DB path、admin port、trace ports、secret master key。

### Runtime Resource

来源：DB domain tables。

特征：

- Admin UI 可创建、修改、禁用。
- 生效范围由 resource type 定义。
- 示例：Provider、Character、Actor、Channel、Route。

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

## Provider 修改语义

```text
Create provider
  立即可用于新 Actor。

Update api_key/base_url/models
  validate -> save secret/config -> bump provider.version。
  新 turn 使用新 version。
  正在执行的 turn 不打断。

Disable provider
  新 turn 不再使用。
  若 Actor 仍引用，Actor status = degraded / needs_provider。

Delete provider
  如果被 Actor 引用，禁止删除，只能 disable。
```

## Character 修改语义

```text
Create character
  立即可用于新 Actor。

Update sections/tool surface
  bump character.version。
  新 conversation 默认使用新版本。
  已 pin 的 active conversation 是否重载，由 Actor policy 决定。

Reset builtin
  恢复到当前 builtin version。
```

建议初期规则：Character 修改对新 turn 生效，但 active actor session 不强制中断；UI 提示“部分长会话可能在 rollover 后完全生效”。

## Actor 修改语义

```text
Update model binding
  新 turn 生效。

Update runtime policy
  新 turn 生效。

Update tool permissions
  新 turn 生效，正在执行的 tool call 不打断。

Disable actor
  新 route 不再选择。
  已 pin context 显示 error，等待管理员 reassign。
```

## Channel 修改语义

```text
Create channel
  完成 auth 后可 start。

Update auth/config
  adapter reload。
  如果不能热 reload，则标记 restart required。

Disable channel
  stop adapter，不再接收消息。
```

## Route 修改语义

```text
Update route rule
  立即影响未 pin 的 context。
  已 pin context 不受影响。

Reassign context
  立即影响该 context 后续消息。
```

## 统一写路径

所有 runtime 修改必须走统一服务：

```text
validate
  -> write DB
  -> bump version if needed
  -> patch in-memory registry
  -> notify affected component
  -> return applied/errors
```

禁止新的代码路径只改内存不写 DB。
