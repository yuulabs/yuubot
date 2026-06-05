# Code Review: Anti-Pattern Analysis & Architecture Refinement

## 核心洞察：边界序列化，内部类型化

**问题根源**：`dict[str, object]` 从存储边界泄漏到整个内部流程。正确做法是——dict 只存在于序列化边界的一瞬间，内部全程使用 msgspec Struct。

```
存储层 (JSON/dict)  ←→  序列化边界  ←→  内部 (msgspec Struct)
                         ↑ 唯一转换点
```

当前代码的问题不是"缺少 typed accessor"，而是**根本没有在边界做转换**。dict 从 DB 读出来后原样传递，从 HTTP 进来后也原样传递，导致整个内部流程都在做 `.get()` + `isinstance()`。

---

## 1. dict 泄漏的三个层次

### 层次 A：Record 字段 — `config: dict[str, object]`

**当前**：
```python
# records.py
class IntegrationRecord(msgspec.Struct):
    config: dict[str, object] = msgspec.field(default_factory=dict)

class ActorRecord(msgspec.Struct):
    config: dict[str, object] = msgspec.field(default_factory=dict)
```

**泄漏路径**：
- `IntegrationRecord.config` → `IntegrationCore._enable_locked()` → `factory.create(record)` → `validate_integration_config(dict(record.config), schema=...)` → 又转回 dict
- `ActorRecord.config` → `commands.py._normalize_actor_payload()` → 手动 `.pop()` / `.setdefault()` 操作
- `echo.py:354` — `record.config.get("source_path", ...)` — 直接从 dict 取值

**问题**：config 在存储时是 JSON，读出后是 `dict[str, object]`，消费时又要 validate 回 Struct。这个循环说明 Record 层面就不该存 dict。

**正确做法**：Record 存 JSON 字符串（或保持 dict 用于 ORM 存储），但在**从 Repository 读出时**立即根据 factory 的 `config_schema` 反序列化为具体 Struct。内部代码只接触类型化的 config。

```python
# 不是这样：
record = await repository.get(IntegrationORM, integration_id)
config_value = record.config.get("source_path")  # untyped

# 而是这样：
record = await repository.get(IntegrationORM, integration_id)
config = record.typed_config(EchoIntegrationConfig)  # 边界处转换
config.source_path  # typed
```

或者更彻底：让 `IntegrationRecord` 的 config 字段在 ORM 层面存 JSON，在 record 层面用一个泛型包装：

```python
class IntegrationRecord(msgspec.Struct):
    name: str
    _raw_config: dict[str, object] = msgspec.field(default_factory=dict, name="config")
    id: str = ""
    enabled: bool = True
    # ...

    def typed_config(self, schema: type[T]) -> T:
        """在消费边界将 raw config 反序列化为具体类型。"""
        return msgspec.convert(self._raw_config, type=schema, strict=False)
```

这样 `record.config` 在存储/传输时是 dict（序列化边界），但在消费时通过 `typed_config()` 转为具体类型。内部代码不再碰 dict。

### 层次 B：HTTP 请求体 — `payload: dict[str, object]`

**当前**（commands.py，约 80 行手动解析）：
```python
async def create(self, request: Request) -> JSONResponse:
    payload = await self._parse_json_body(request)  # dict[str, object]
    if not payload.get("id"):
        payload["id"] = str(uuid.uuid4())
    # ... 20 more lines of .get() and isinstance() ...
    record = self._decode_payload(orm_type, payload)
```

**正确做法**：用 msgspec Struct 在 HTTP 边界做一次性解析：

```python
class CreateActorRequest(msgspec.Struct, forbid_unknown_fields=False):
    id: str = ""
    name: str = ""
    character_id: str = ""
    llm_backend_id: str = ""
    model: str = ""
    # ... 所有字段带默认值

class CreateIntegrationRequest(msgspec.Struct, forbid_unknown_fields=False):
    id: str = ""
    name: str = ""
    config: dict[str, object] = msgspec.field(default_factory=dict)
    enabled: bool = True

# handler 变成：
async def create(self, request: Request) -> JSONResponse:
    payload = await self._parse_json_body(request)
    if isinstance(payload, JSONResponse):
        return payload
    req = msgspec.convert(payload, type=CreateActorRequest, strict=False)
    # req.name, req.character_id 等全部类型化
```

这消除了 `_normalize_actor_payload()` 的全部手动 `.pop()` / `.setdefault()` 逻辑。

### 层次 C：事件数据 — `event.data: dict[str, object]`

**当前**（conversations.py, observability.py）：
```python
# conversations.py:296-301
entity_id = event.data.get("entity_id")
if isinstance(entity_id, str) and entity_id in self._agent_to_conversations:
    ...
parent_id = event.data.get("parent_id")
if isinstance(parent_id, str) and parent_id in self._agent_to_conversations:
    ...
```

这是 yuuagents 的 `RuntimeEvent.data` 传过来的 dict。yuubot 无法控制上游类型，但可以在**接收边界**做一次转换：

```python
@dataclass(frozen=True)
class AgentEventIdentity:
    """从 RuntimeEvent.data 中提取的类型化身份信息。"""
    agent_id: str
    entity_id: str = ""
    parent_id: str = ""

    @classmethod
    def from_event(cls, event: RuntimeEvent) -> AgentEventIdentity:
        data = event.data
        return cls(
            agent_id=event.agent_id or "",
            entity_id=str(data.get("entity_id") or ""),
            parent_id=str(data.get("parent_id") or ""),
        )
```

这样消费端只接触 `identity.agent_id`，不再散落 `.get()` + `isinstance()`。

---

## 2. getattr 影子协议

### 问题

`model_factory.py` 通过 `type()` 动态创建 ORM 类并 stamp 上 `_yuubot_schema_type` 等属性。后续代码全部通过 `getattr(row_type, "_yuubot_schema_type")` 访问。类型检查器看不到这些属性，拼错名只有运行时才知道。

### 正确做法：引入 Protocol + 类型化访问器

```python
# resources/store/protocol.py
from typing import Protocol, TypeVar
from tortoise import Model
import msgspec

RecordT = TypeVar("RecordT", bound=msgspec.Struct)

class ResourceModel(Protocol):
    """Tortoise ORM models derived from resource records must carry these attributes."""
    _yuubot_schema_type: type[msgspec.Struct]
    _yuubot_schema_fields: frozenset[str]
    _yuubot_generated_fields: frozenset[str]
    _yuubot_references: dict[str, object]

def schema_type_of(orm_type: type[Model]) -> type[msgspec.Struct]:
    """Typed accessor — replaces getattr(orm_type, "_yuubot_schema_type")."""
    return orm_type._yuubot_schema_type

def schema_fields_of(orm_type: type[Model]) -> frozenset[str]:
    return orm_type._yuubot_schema_fields

def generated_fields_of(orm_type: type[Model]) -> frozenset[str]:
    return orm_type._yuubot_generated_fields

def references_of(orm_type: type[Model]) -> dict[str, object]:
    return orm_type._yuubot_references
```

然后全局替换：
- `getattr(row_type, "_yuubot_schema_type")` → `schema_type_of(row_type)`
- `getattr(type(row), "_yuubot_schema_type")` → `schema_type_of(type(row))`
- `getattr(row_type, "_yuubot_schema_fields")` → `schema_fields_of(row_type)`
- 等等

`model_factory.py` 中 `resource_model()` 函数创建的类天然满足 `ResourceModel` Protocol（因为它设置了这些属性），所以不需要显式继承。

### IntegrationFactory 可选属性

`IntegrationFactory` Protocol 只声明了 `name`、`capability_specs()`、`create()` 三个必须成员。但实际运行时，代码还在检查 `config_schema`、`description`、`routes` 这三个"可选但真实存在"的属性。检查方式是 `getattr` + 默认值。

Protocol 需要加强，把这些属性声明为协议的一部分。`getattr` 总是不好的——拼错方法名不会报错，只是静默跳过。

---

### 设计变更：集成路由挂载时机

**当前**：`collect_routes()` 在 daemon 启动时一次性收集所有内置集成的 HTTP 入口端点，作为固定路由挂载到 Starlette app。外部插件没有 route，它通过 `/ingest` 主动推消息进来。

**问题**：

1. 外部插件是独立进程，监听在 `127.0.0.1:随机端口`。外部系统（QQ、Telegram 等）需要给插件推消息，但只能访问 daemon 的公网地址。daemon 上没有属于这个插件的路由，外部系统无法把消息推到插件。
2. 路由在 app 创建时就固定了，启动后新启用的集成无法注册路由。

**新设计**：route 挂载揉入集成启用阶段。每当一个 integration 启用时，挂载它的 route；禁用时，卸载。不再有专门的 `collect_routes()` 步骤。

这意味着：
- 内置集成的 route 在 daemon 启动后、集成 enable 时挂载
- 外部插件的 route 也在 enable 时挂载——daemon 作为反向代理，将 `/integration/{name}/...` 的请求转发到插件的本地 HTTP server
- `ExternalPluginManifest.ingress.routes`（已声明但未使用）将用于生成反代路由
- `IntegrationFactory.routes()` 和 `getattr(factory, "routes", None)` 的鸭子检查被替换为 Protocol 声明

---

## 3. 类型身份分派违反开闭原则

### 当前

```python
# service.py
def _lifecycle_realm(orm_type: type[Model]) -> str:
    if orm_type is IntegrationORM:
        return "integrations"
    if orm_type is ActorORM:
        return "actors"
    return "unknown"

# service.py set_enabled()
if realm == "integrations":
    await self.integrations.reconcile(...)
elif realm == "actors":
    await self.actors.reconcile()
```

### 正确做法：注册表携带行为描述符

```python
@dataclass
class ResourceTypeDescriptor:
    slug: str
    orm_type: type[Model]
    lifecycle_realm: str = ""  # "integrations" | "actors" | ""
    has_lifecycle: bool = False

# 注册时：
registry.register(ResourceTypeDescriptor(
    slug="integrations", orm_type=IntegrationORM,
    lifecycle_realm="integrations", has_lifecycle=True,
))
registry.register(ResourceTypeDescriptor(
    slug="actors", orm_type=ActorORM,
    lifecycle_realm="actors", has_lifecycle=True,
))

# 使用时：
descriptor = registry.get_descriptor(slug)
if descriptor.has_lifecycle:
    ...
```

---

## 4. 能力查找线性扫描

```python
# 当前：O(n) 扫描
def _find_capability(self, capability_id: str) -> AnyCapability:
    for (_, cap_id), capability in self._capabilities_index.items():
        if cap_id == capability_id:
            return capability

# 修正：添加二级索引
_capability_by_id: dict[str, AnyCapability] = field(default_factory=dict, init=False)
_integration_by_capability: dict[str, str] = field(default_factory=dict, init=False)
```

---

## 5. 其他 getattr 残留

| 位置 | 当前 | 修正 |
|------|------|------|
| `assembly.py:416-418` | `getattr(store.usage, "input_tokens", 0)` | 提议 yuuagents 暴露 `input_tokens` 属性，或本地封装 `def token_count(usage) -> int` |
| `assembly.py:547` | `getattr(agent.budget, "_usage", None)` | 同上——提议 `Budget.reset_usage(key)` 方法 |
| `app.py:777` | `getattr(actor, "run_schedule_tool", None)` | 在 Actor Protocol 上声明 `run_schedule_tool` 或用 `hasattr` + Protocol 拆分 |
| `app.py:980` | `getattr(value, "isoformat", None)` | `isinstance(value, datetime)` |
| `validators.py:29,35` | `getattr(character, "id", None)` | `character.id if hasattr(character, 'id') else ...` → 用类型 narrowing |
| `conversations.py:274` | `getattr(actor, "_runtime", None)` | 在 SimpleLoopActor 上暴露 `runtime` 属性 |

---

## 优先级排序

| 优先级 | 反模式 | 影响 | 工作量 |
|--------|--------|------|--------|
| **P0** | dict 泄漏（Record config + HTTP payload） | 类型安全、正确性 | 中 |
| **P0** | getattr 影子协议（ORM 元数据） | 重构安全性 | 低 |
| **P1** | 类型身份分派 | 开闭原则 | 中 |
| **P1** | 能力查找线性扫描 | 性能 | 低 |
| **P2** | 其他 getattr 残留 | 代码卫生 | 低 |

---

## 实施策略

**核心原则**：在序列化边界做一次转换，内部全程类型化。

1. **Step 1**: 引入 `ResourceModel` Protocol + 类型化访问器函数，替换所有 `getattr(row_type, "_yuubot_*")` 调用
2. **Step 2**: 为 `IntegrationRecord.config` 和 `ActorRecord.config` 添加 `typed_config()` 方法，在消费边界做 msgspec 转换
3. **Step 3**: HTTP 请求体用 msgspec Struct 做边界解析，消除 `_normalize_actor_payload()` 和 `.get()` 链
4. **Step 4**: `ResourceTypeDescriptor` 注册表替代 `_lifecycle_realm()` if/elif
5. **Step 5**: 能力二级索引
6. **Step 6**: 清理其他 getattr 残留

每步独立可验证：`uv run ruff check` + `uv run ty check` + `uv run pytest`。