# 04. Gateway / IngressRule

Gateway 的目标保持很小：路由消息、管理 actor mailbox。v2 core 只内置 Web 内置入口；Discord、Telegram、QQ/NapCat 等渠道作为 Integration 接入。

## Gateway 架构

Gateway 是单层投递引擎。没有独立的 "domain layer" 或 channels 表——路由完全由 `actor_ingress_rules` + 启用 actor 列表生成的 `RouteBindings` 表达。

```python
@dataclass
class Gateway:
    routes: RouteBindings              # 不可变 ActorIngressRule 快照
    _mailboxes: dict[str, Mailbox]     # actor_id → Mailbox

    def open_integration(integration_id: str) -> IntegrationIngress
    def get_mailbox(actor_id: str) -> Mailbox
    def close_mailbox(actor_id: str) -> None
    def update_bindings(bindings: RouteBindings) -> None
    async def ingest(message: IncomingMessage) -> None
```

数据流：

```text
ResourceRepository (写 actor_ingress_rules / actors)
  → EventBus.publish(ResourceChanged)
    → DaemonRefreshDispatcher
      → RouteBindingService.reload()
        → load_route_bindings(repository) → new RouteBindings
        → Gateway.update_bindings(new_bindings)
```

## Message Flow

```text
入站：
外部事件 → Integration (协议转换)
       → IntegrationIngress.emit(IncomingMessage)
       → Gateway.ingest()  → 遍历 RouteBindings 匹配 → 投递到 Mailboxes

出站：
Actor → 调用 Integration capability (如 search.query / im.qq.send) → 外部平台
```

具体流程：

```text
1. Integration 接收外部事件（WebSocket、webhook、轮询等）
2. Integration 将外部格式转换为 IncomingMessage
3. Integration 调用 ingress.emit(message)
   ingress 自动盖戳 source = MessageSource(producer="integration",
                                           id=integration_id,
                                           path=message.source.path)
4. Gateway.ingest(message) 遍历 RouteBindings.rules
   对每条 enabled rule，fnmatch (source.id, source.path, kind) 命中则收集 actor_id
5. 命中 actor 集合去重后，依次 put 到对应 Mailbox
6. Actor 从 Mailbox 读取消息，处理，通过 Integration capability 回复
```

## IncomingMessage / MessageSource

```python
class MessageSource(msgspec.Struct):
    producer: str = "integration"   # "integration" | "system"
    id: str = ""                    # integration_id / "system:<actor_id>"
    path: str = ""                  # integration 自定义子路径

class IncomingMessage(msgspec.Struct):
    message_id: str
    sender_id: str
    source: MessageSource = MessageSource()
    kind: str = ""                   # "private" / "group" / "system" 等，由 Integration 设定
    sender_name: str = ""
    segments: tuple[Segment, ...] = ()
    text: str = ""
    timestamp: int = ...
```

平台特定字段通过子类扩展，而非无类型 metadata 字段：

```python
class QQIncomingMessage(IncomingMessage):
    group_id: str = ""

class DiscordIncomingMessage(IncomingMessage):
    guild_id: str = ""
    thread_id: str = ""
```

Gateway 只看基类接口（`source`、`kind`、`sender_id`），不需要知道平台细节。Integration 内部如何把 `group_id` 编码进 `source.path`（例如 `"group:42"`）由 Integration 自行决定，路由层只看 fnmatch 命中。

## RouteBindings

```python
@dataclass
class ActorIngressRule:
    actor_id: str
    source_id_pattern: str
    source_path_pattern: str
    kind_patterns: tuple[str, ...]

    def matches(self, message: IncomingMessage) -> bool:
        return (
            fnmatchcase(message.source.id, self.source_id_pattern)
            and fnmatchcase(message.source.path, self.source_path_pattern)
            and any(fnmatchcase(message.kind, p) for p in self.kind_patterns)
        )

@dataclass
class RouteBindings:
    rules: tuple[ActorIngressRule, ...]   # 不可变快照

    def resolve(self, message: IncomingMessage) -> tuple[str, ...]
    def actor_ids(self) -> tuple[str, ...]
```

`load_route_bindings(repository)` 从 `actor_ingress_rules` 表 + enabled actors 构建快照：

- 每条 enabled `ActorIngressRuleRecord`（其 actor_id 必须属于 enabled actor）转成一条 `ActorIngressRule`。
- 每个 enabled actor_id 自动追加一条 system rule：`source_id_pattern = "system:<actor_id>"`、`source_path_pattern = "**"`、`kind_patterns = ("*",)`。这条规则给 actor 间消息和定时触发使用，不需要管理员配置。

Daemon 在 `actors` / `actor_ingress_rules` 表变更时调用 `RouteBindingService.reload()`，构建新快照后 `Gateway.update_bindings(new)` 替换旧快照。已有 in-flight 消息不受影响。

## 没有 channels 表

v2 不维护独立的 `channels`、`channel_targets`、`route_rules` 表。理由：

- Integration 已经唯一标识了一个外部连接源，`MessageSource.id == integration_id` 就是它的"频道身份"。
- 多频道（如 QQ 多群、Discord 多 guild）通过 `MessageSource.path` 在同一 integration 下区分，由 ingress rule 的 `source_path_pattern` 选取。
- 如果 UI 需要"频道列表"视图，从 `actor_ingress_rules` + 历史消息聚合即可派生，不需要落表。

## Web Chat

Web Chat 是内置入口，遵循 Gateway 模型——它就是一个 builtin integration（`integration_id = "web-admin"` 或类似），通过 `IntegrationIngress.emit()` 投递消息。

Web Chat 不应是单一固定会话。Admin UI 至少支持：

- 创建多个 Web dialog，每个 dialog 用一个独立 `source.path`（如 `dialog:<uuid>`）。
- 从任意 dialog 发送消息，消息路径必须与其他 Integration 一样进入 Gateway（同一个 `IntegrationIngress.emit` 路径）。
- 通过 `actor_ingress_rules` 把不同 dialog 路由到不同 actor，用于验证投递机制。

可靠性边界：

```text
Admin 后端收到 WebSocket message → 写入 inbound_queue（DB）成功 → ack 前端
Daemon worker 从 inbound_queue 读取 → 通过 web ingress emit 给 Gateway → 标记 done
```

daemon 是否在线不影响"消息已收到"的判断。

建议队列表：

```sql
CREATE TABLE inbound_queue (
    id INTEGER PRIMARY KEY,
    integration_id TEXT NOT NULL,        -- web-admin 等
    source_path TEXT NOT NULL,           -- dialog:<uuid>
    client_nonce TEXT,
    payload JSON NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    locked_at TIMESTAMP,
    locked_by TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

状态：

```text
pending -> processing -> done | failed
```

daemon 重启时可以重新领取 `pending` 或超时 `processing` 的消息。

## 非平台概念

以下不在 v2 Gateway 模型中：

- **Channel 表 / ChannelResource**：平台不持有"频道"一等对象，只有 Integration + ActorIngressRule。
- **Context / Session**：平台不知道会话。Integration 把会话语义编码进 `source.path`（如 `private:user-7` / `group:42`），actor 内部如何使用是它自己的事。
- **Context pinning / reassign**：没有"pin actor 到会话"的概念。`actor_ingress_rules` 就是全部投递行为。
- **RouteRule / priority / text match**：不做 priority、不做正文文本匹配。复杂语义通过多条 ingress rule 的 fnmatch 模式表达。
