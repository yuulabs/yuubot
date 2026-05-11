# Gateway & Channel Contract

Gateway 的核心目标很小：让不同平台（QQ、Discord、Telegram、Web）都能把消息变成同一种内部事件，然后按 Context 分发给 actor。

核心不理解 QQ/OneBot/Discord raw event。平台差异只存在于各自的 channel adapter。

```
Raw platform event
  -> ChannelAdapter
  -> IncomingMessage(context=ContextRef(...))
  -> Gateway get/create Context
  -> InboundMessage(ctx_id=...)
  -> Dispatcher / Routing
  -> Actor
```

发送方向相反：

```
Actor reply with ctx_id
  -> Gateway loads Context
  -> Gateway selects adapter by Context.channel
  -> adapter.send(ctx, message)
```

---

## Concepts

### Channel

Channel 是一个平台适配器，例如：

- `qq`
- `discord`
- `telegram`
- `web`

Channel adapter 负责连接平台 API、解析平台事件、发送平台消息。

### Context

Context 是一个稳定会话。

例子：

- QQ 群
- QQ 私聊
- Discord channel
- Discord thread
- Telegram topic
- Web session

Context 由 `(channel, key)` 唯一确定。

### Actor

Actor 是消息处理者，例如 `yuu`、`shiori`、`forum_bot`。

Gateway 不关心 actor 怎么运行，只负责把消息路由到 actor。

---

## Minimal Contract

新增一个 channel，只需要实现一个 adapter：

```python
class ChannelAdapter(Protocol):
    channel: str

    async def start(
        self,
        emit: Callable[[IncomingMessage], Awaitable[None]],
    ) -> None:
        """Connect to the platform and emit IncomingMessage for each message."""
        ...

    async def send(self, ctx: Context, text: str, **kwargs) -> None:
        """Send message back to the platform. Each integration defines its own API."""
        ...

    async def stop(self) -> None:
        """Close platform connections."""
        ...
```

Adapter 收到平台消息后，必须 emit 一个 `IncomingMessage`。

```python
class ContextRef(msgspec.Struct):
    channel: str          # "qq", "discord", "telegram", ...
    key: str              # stable conversation id inside the channel
    kind: str             # "private", "group", "thread", "session", "other"
    label: str = ""
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


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

这是 channel 和框架之间唯一必须稳定的协议。

---

## ContextRef Rules

`ContextRef` 是整个 gateway 的关键。Adapter 必须回答：这条消息属于哪个会话？

规则：

1. 同一个会话必须生成同一个 `(channel, key)`。
2. 不同会话必须生成不同 `(channel, key)`。
3. `kind` 用于默认路由。
4. `metadata` 由 adapter 拥有，必须包含 `send()` 需要的结构化信息。
5. 核心框架不解析 `key`，也不理解平台字段。

例子：

```python
# QQ group, inside QQ adapter only
ContextRef(
    channel="qq",
    key=f"group:{group_id}",
    kind="group",
    label=f"QQ群 {group_id}",
    metadata={"group_id": str(group_id)},
)
```

```python
# QQ private, inside QQ adapter only
ContextRef(
    channel="qq",
    key=f"private:{user_id}",
    kind="private",
    label=f"QQ 私聊 {user_id}",
    metadata={"user_id": str(user_id), "is_master": user_id == master_id},
)
```

```python
# Discord text channel
ContextRef(
    channel="discord",
    key=f"guild:{guild_id}/channel:{channel_id}",
    kind="group",
    label=f"{guild_name} / {channel_name}",
    metadata={
        "guild_id": guild_id,
        "channel_id": channel_id,
        "channel_type": "text",
    },
)
```

```python
# Discord thread
ContextRef(
    channel="discord",
    key=f"guild:{guild_id}/channel:{channel_id}/thread:{thread_id}",
    kind="thread",
    label=f"{guild_name} / {thread_name}",
    metadata={
        "guild_id": guild_id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "channel_type": "thread",
    },
)
```

QQ 兼容逻辑不进入核心抽象。`group_id`、`user_id`、OneBot message array 等细节都留在 QQ adapter 内部。

---

## Gateway Ingest

Gateway 收到 `IncomingMessage` 后做三件事：

```python
ctx = await get_or_create_context(incoming.context)
message = inbound_from_incoming(incoming, ctx.id)
await dispatcher.dispatch_message(message)
```

`get_or_create_context()` 的行为：

```python
async def get_or_create_context(ref: ContextRef) -> Context:
    ctx, created = await Context.get_or_create(
        channel=ref.channel,
        key=ref.key,
        defaults={
            "kind": ref.kind,
            "label": ref.label,
            "metadata": ref.metadata,
        },
    )
    if not created:
        await update_context_snapshot(ctx, ref)
    return ctx
```

Context 保存的是会话快照：

```python
class Context(Model):
    id: int
    channel: str
    key: str
    kind: str
    label: str
    metadata: dict[str, Any]
    last_message_at: datetime | None
    archived: bool
    created_at: datetime
```

实现上可以保留旧字段用于迁移，但新 gateway 抽象只依赖这些字段。

---

## Routing

Gateway 内部只维护一个极简映射：`channel_id → actor_ids`。

- 没有多维匹配、没有 priority、没有 Context pinning。
- 复杂的路由需求（如不同群用不同 actor）属于 Actor 内部实现，不进入平台层。

路由在 daemon 装配层配置，通过 `RouteBindings` 推入 Gateway：`Gateway.update_bindings(bindings)`。

---

## Sending

Actor 和 command 只持有 `ctx_id`。出站通过 Integration 能力调用（如 `im.qq.send`）。

```python
await im.send(ctx_id, text="hello")
```
```

Adapter 自己解释 `ctx.metadata`。

```python
class DiscordAdapter:
    channel = "discord"

    async def send(self, ctx, message):
        thread_id = ctx.metadata.get("thread_id")
        channel_id = ctx.metadata["channel_id"]
        if thread_id:
            await discord.send_to_thread(thread_id, message.text)
        else:
            await discord.send_to_channel(channel_id, message.text)
```

```python
class QQAdapter:
    channel = "qq"

    async def send(self, ctx, message):
        if ctx.kind == "group":
            await onebot.send_group_msg(ctx.metadata["group_id"], message.text)
        elif ctx.kind == "private":
            await onebot.send_private_msg(ctx.metadata["user_id"], message.text)
```

---

## Adding A Channel

Checklist:

1. Create `src/yuubot/channels/<name>.py`.
2. Implement `ChannelAdapter`.
3. Convert platform events into `IncomingMessage`.
4. Ensure `ContextRef.key` is stable.
5. Put send target fields in `ContextRef.metadata`.
6. Register the adapter at daemon startup.
7. Add config under `channels.<name>`.
8. Bind the channel to actor targets in daemon assembly (one `channel_id → actor_ids` mapping).

No new actor code is required.

---

## Non-goals

Do not add these until needed:

- dynamic plugin loading
- channel hot reload
- full capability negotiation
- generic thread manager
- channel-specific fields on core message types
- route rule tables, priority matching, or per-context routing overrides

The extension point is intentionally small: `ChannelAdapter + ContextRef + IncomingMessage`.
