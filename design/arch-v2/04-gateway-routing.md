# 04. Gateway / Channel / Context / Route

Gateway 的目标保持很小：不同平台的 adapter 把消息变成统一的 `IncomingMessage`，Gateway 负责 Context 创建、路由和回复发送。

## Message Flow

```text
Raw platform event
  -> ChannelAdapter
  -> IncomingMessage(context=ContextRef(...))
  -> Gateway get/create Context
  -> Route Engine select Actor
  -> Actor Runtime
  -> Gateway send OutboundMessage through ChannelAdapter
```

## ChannelAdapter Contract

```python
class ChannelAdapter(Protocol):
    channel: str

    async def start(
        self,
        emit: Callable[[IncomingMessage], Awaitable[None]],
    ) -> None:
        ...

    async def send(self, ctx: Context, message: OutboundMessage) -> None:
        ...

    async def stop(self) -> None:
        ...
```

## ContextRef

```python
class ContextRef(msgspec.Struct):
    channel: str
    key: str
    kind: str
    label: str = ""
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)
```

规则：

1. 同一会话必须生成同一个 `(channel, key)`。
2. 不同会话必须生成不同 `(channel, key)`。
3. Gateway 核心不解析 `key`，平台字段留在 adapter 的 `metadata`。
4. `metadata` 必须包含 `send()` 需要的结构化字段。
5. Gateway 上线前必须迁移并创建 `UNIQUE(channel, key)`；已有重复数据先清理。

## IncomingMessage

```python
class IncomingMessage(msgspec.Struct):
    context: ContextRef
    message_id: str
    sender_id: str
    sender_name: str = ""
    segments: list[Segment] = msgspec.field(default_factory=list)
    text: str = ""
    timestamp: int = 0
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)
```

## OutboundMessage

```python
class OutboundMessage(msgspec.Struct):
    text: str = ""
    segments: list[Segment] = msgspec.field(default_factory=list)
    reply_to: str = ""
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)
```

## Route Resolution

推荐解析顺序：

```text
1. Context.actor_id if pinned and actor enabled
2. Route rules by priority
3. Channel default_private_actor / default_group_actor
4. System default private/group/thread/other actor
5. No route -> reject with operator-visible error
```

首次解析后，默认 pin：

```text
if context.actor_id is null:
  actor = resolve_route(context, incoming)
  context.actor_id = actor.id
```

这样老会话不会因为默认 Actor 修改而突然换人格。管理员可在 UI 中手动 reassign。

## Route Rule Match

Route rule match 支持：

- `channel`
- `kind`
- `metadata.<key>`
- `sender_id`
- `text_contains`
- `text_regex`
- `time_window`

实现初期建议保持简单，只实现 `channel/kind/metadata exact match`。

## Web Chat Reliability Boundary

Web Chat 是 Channel，遵循 Gateway 模型。

可靠性边界：

```text
Admin 后端收到 WebSocket message -> 写入 DB 成功 -> ack 前端
```

daemon 是否在线不影响“消息已收到”的判断。

建议队列表：

```sql
CREATE TABLE inbound_queue (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    context_id INTEGER NOT NULL,
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
pending -> processing -> done
                 -> failed
```

daemon 重启时可以重新领取 `pending` 或超时 `processing` 的消息。
