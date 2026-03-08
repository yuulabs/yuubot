# 数据库 Schema 设计

yuubot 使用 SQLite，通过 Tortoise ORM 管理模型和查询。使用 WAL 模式保证并发读写性能。

## Tortoise Model

所有 Model 定义在 `yuubot.core.models` 中。

### Context — ctx_id 映射

```python
class Context(Model):
    id = fields.IntField(pk=True)  # ctx_id
    type = fields.CharField(max_length=16)  # 'private' | 'group'
    target_id = fields.BigIntField()  # group_id 或 user_id
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "contexts"
        unique_together = (("type", "target_id"),)
```

**说明**：
- id 自增整数，首次收到某群聊/私聊消息时自动分配
- type + target_id 唯一确定一个 ctx
- Recorder 启动时热加载到内存 dict

### MessageRecord — 消息落盘

```python
class MessageRecord(Model):
    id = fields.IntField(pk=True)
    message_id = fields.BigIntField(null=True)  # OneBot 消息 ID
    ctx = fields.ForeignKeyField("models.Context", related_name="messages")
    user_id = fields.BigIntField()
    nickname = fields.CharField(max_length=64, null=True)
    content = fields.TextField()  # 纯文本（用于搜索）
    raw_message = fields.TextField()  # 原始消息段 JSON
    timestamp = fields.DatetimeField()

    class Meta:
        table = "messages"
        indexes = (("ctx_id", "-timestamp"),)
```

**说明**：
- `content` 存储纯文本（从消息段中提取），用于全文搜索
- `raw_message` 存储完整的消息段 JSON，用于精确还原
- FTS5 虚拟表通过 raw SQL 创建（ORM 不支持虚拟表）

### FTS5 全文搜索（raw SQL）

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
    VALUES ('delete', old.id, old.content);
END;
```

FTS5 是 SQLite 特有功能，无法用 ORM 表达，保留 raw SQL。

### Memory — 记忆系统

```python
class Memory(Model):
    id = fields.IntField(pk=True)
    content = fields.TextField()
    ctx = fields.ForeignKeyField("models.Context", related_name="memories", null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    last_accessed = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "memories"
        indexes = (("ctx_id",), ("last_accessed",))
```

### MemoryTag — 记忆标签（多对多）

```python
class MemoryTag(Model):
    memory = fields.ForeignKeyField("models.Memory", related_name="tags", on_delete=fields.CASCADE)
    tag = fields.CharField(max_length=64)

    class Meta:
        table = "memory_tags"
        unique_together = (("memory_id", "tag"),)
        indexes = (("tag",),)
```

### ImageEntry — 图片/表情包库

```python
class ImageEntry(Model):
    id = fields.IntField(pk=True)
    local_path = fields.CharField(max_length=512, unique=True)
    description = fields.TextField(default="")
    tags = fields.JSONField(default=list)       # ["猫", "搞笑"]
    source_msg_id = fields.BigIntField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "images"
```

**说明**：
- 全局共享，不区分 ctx
- `description` 通过 FTS5 虚拟表 `images_fts` 索引，支持中文搜索
- `tags` 存储为 JSON 数组，应用层过滤
- FTS5 触发器同步 INSERT/DELETE/UPDATE

### RoleRecord — 权限系统

```python
class RoleRecord(Model):
    user_id = fields.BigIntField()
    role = fields.IntField()  # 0=Deny, 1=Folk, 2=Mod, 3=Master
    scope = fields.CharField(max_length=32)  # 'global' 或 group_id

    class Meta:
        table = "roles"
        unique_together = (("user_id", "scope"),)
```

### GroupSetting — 群聊设置

```python
class GroupSetting(Model):
    group_id = fields.BigIntField(pk=True)
    bot_enabled = fields.BooleanField(default=True)
    response_mode = fields.CharField(max_length=16, default="at")
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "group_settings"
```

### EntryMapping — 入口映射

```python
class EntryMapping(Model):
    entry = fields.CharField(max_length=64)
    route = fields.CharField(max_length=128)
    scope = fields.CharField(max_length=32)  # 'global' 或 group_id

    class Meta:
        table = "entry_mappings"
        unique_together = (("entry", "scope"),)
```

### MemoryConfig — 记忆系统配置

```python
class MemoryConfig(Model):
    key = fields.CharField(max_length=64, pk=True)
    value = fields.TextField()

    class Meta:
        table = "memory_config"
```

## 初始化

数据库初始化通过 Tortoise ORM 自动执行：

```python
async def init_db(db_path: str):
    await Tortoise.init(
        db_url=f"sqlite://{db_path}",
        modules={"models": ["yuubot.core.models"]},
    )
    await Tortoise.generate_schemas()

    # WAL 模式 + FTS5（ORM 不支持的部分）
    conn = connections.get("default")
    await conn.execute_query("PRAGMA journal_mode=WAL")
    await conn.execute_query("PRAGMA foreign_keys=ON")
    await conn.execute_script(FTS_SQL)
```

## 并发访问

- **Recorder** 写入 MessageRecord、Context
- **Daemon** 读取 MessageRecord（通过 skills CLI），读写 RoleRecord、GroupSetting
- **Skills CLI** 读写 MessageRecord（search）、Memory、MemoryTag

SQLite WAL 模式支持一写多读，满足需求。

## 数据清理

- **消息**：暂不自动清理，后续可配置保留天数
- **记忆**：自动遗忘系统，定期清理 `last_accessed` 超过 `forget_days` 的记忆
- **FTS 索引**：随消息删除自动同步
