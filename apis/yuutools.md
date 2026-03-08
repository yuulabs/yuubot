# yuutools API 文档

## 概述

yuutools 是一个 Tool 定义与依赖注入框架。通过 `@tool` 装饰器和 `depends()` 函数，开发者可以用 Python 函数定义工具，自动生成 JSON Schema 供 LLM 函数调用使用，同时通过依赖注入分离运行时上下文。

核心设计：
- **装饰器驱动** — `@tool()` 自动内省函数签名，生成 LLM 可用的 JSON Schema
- **依赖注入** — `depends()` 标记的参数对 LLM 隐藏，运行时从上下文解析
- **类型安全** — 自动将 Python 类型注解转为 JSON Schema 片段
- **绑定执行** — `Tool.bind(ctx)` → `BoundTool`，注入依赖后执行

## 快速开始

```python
import yuutools as yt

# 1. 定义 tool
@yt.tool(
    params={"query": "搜索关键词", "max_results": "最大结果数"},
    description="搜索网页",
)
async def web_search(
    query: str,
    max_results: int = 5,
    api_key: str = yt.depends(lambda ctx: ctx.api_key),  # 依赖注入，对 LLM 隐藏
) -> str:
    # api_key 从 ctx 注入，LLM 只看到 query 和 max_results
    return f"搜索 {query}，使用 key={api_key}"

# 2. 注册到 ToolManager
manager = yt.ToolManager([web_search])

# 3. 生成 JSON Schema（给 LLM 用）
specs = manager.specs()
# [{"type": "function", "function": {"name": "web_search", ...}}]

# 4. 绑定上下文并执行
ctx = MyContext(api_key="sk-...")
bound = manager["web_search"].bind(ctx)
result = await bound.run(query="Python 教程", max_results=3)
```

## `@tool` 装饰器

### 签名

```python
def tool(
    *,
    params: dict[str, str] | None = None,
    name: str = "",
    description: str = "",
) -> Callable[[Callable[..., Any]], Tool[Any]]
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `params` | `dict[str, str] \| None` | 否 | `None` | 参数名 → 描述的映射 |
| `name` | `str` | 否 | `""` | 工具名称，空字符串时使用函数的 `__name__` |
| `description` | `str` | 否 | `""` | 工具描述，空字符串时取函数 docstring 第一行，再无则用函数名 |

### 行为

1. 通过 `inspect.signature(fn)` 获取函数签名
2. 通过 `typing.get_type_hints(fn)` 获取类型注解（安全方式，异常时返回空 dict）
3. 遍历所有参数：
   - 若参数默认值是 `DependencyMarker` → 记为依赖参数，**不暴露给 LLM**
   - 否则 → 创建 `ParamSpec`，包含名称、JSON Schema、描述、是否必填
4. 未标注类型的参数默认当作 `str`
5. 返回 `Tool` 实例

### 示例

```python
@yt.tool(
    params={"command": "要执行的 bash 命令", "timeout": "超时秒数"},
    name="execute_bash",
    description="在 Docker 容器中执行 bash 命令",
)
async def execute_bash(
    command: str,
    timeout: int = 120,
    container: str = yt.depends(lambda ctx: ctx.docker_container),
    docker: DockerExecutor = yt.depends(lambda ctx: ctx.docker),
) -> str:
    ...
```

生成的 JSON Schema 中只有 `command` 和 `timeout` 两个参数，`container` 和 `docker` 对 LLM 完全隐藏。

## `depends()` 依赖注入

### 签名

```python
def depends(resolver: Callable[[Any], T]) -> T
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `resolver` | `Callable[[ctx], T]` | 接受上下文对象，返回依赖值的函数 |

**返回值：** 运行时返回 `DependencyMarker`，类型检查器看到的返回类型是 `T`（resolver 的返回类型）。

### DependencyMarker

```python
class DependencyMarker:
    resolver: Callable[[Any], Any]
```

标记参数为依赖注入项。在工具调用时，resolver 函数以绑定的上下文为参数被调用，产出实际值。

### 注意事项

- **resolver 必须是同步函数**。`Tool.resolve_deps()` 会检查返回值是否为 awaitable，若是则抛出 `TypeError`：

  ```
  Dependency resolver for 'xxx' returned an awaitable.
  Use a sync resolver or wrap in asyncio.run().
  ```

- resolver 在每次 `BoundTool.run()` 时调用，因此可以返回动态值
- 通过类型标注保持 IDE 的代码补全和类型检查

### 示例

```python
# 简单 lambda
db: Database = yt.depends(lambda ctx: ctx.database)

# 命名函数
def get_api_key(ctx) -> str:
    return os.environ[ctx.api_key_env]

api_key: str = yt.depends(get_api_key)
```

## ToolManager

工具的注册中心，按名称索引。

### 构造函数

```python
class ToolManager(Generic[Ctx]):
    def __init__(self, tools: list[Tool[Ctx]] | None = None) -> None
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `tools` | `list[Tool] \| None` | 否 | `None` | 初始工具列表 |

### 方法

| 方法 | 签名 | 说明 |
|------|------|------|
| `register()` | `register(t: Tool) -> None` | 注册工具，名称重复时抛 `ValueError` |
| `__getitem__()` | `__getitem__(name: str) -> Tool` | 按名称获取工具，不存在抛 `KeyError` |
| `__contains__()` | `__contains__(name: str) -> bool` | 检查工具是否存在 |
| `__iter__()` | `__iter__()` | 遍历所有 `Tool` 对象 |
| `__len__()` | `__len__() -> int` | 工具数量 |
| `specs()` | `specs() -> list[dict]` | 生成所有工具的 JSON Schema dict 列表 |

### names 属性

```python
@property
def names(self) -> list[str]
```

返回所有已注册的工具名称列表。

### specs() 返回格式

```python
manager.specs()
# [
#     {
#         "type": "function",
#         "function": {
#             "name": "web_search",
#             "description": "搜索网页",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "query": {"type": "string", "description": "搜索关键词"},
#                     "max_results": {"type": "integer", "description": "最大结果数"}
#                 },
#                 "required": ["query"]
#             }
#         }
#     },
#     ...
# ]
```

## Tool & BoundTool

### Tool

由 `@tool` 装饰器创建，不应直接实例化。

```python
@attrs.define(slots=True)
class Tool(Generic[Ctx]):
    fn: Callable[..., Any]                    # 被装饰的函数
    spec: ToolSpec                             # 工具规格
    _dep_params: dict[str, DependencyMarker]   # 依赖参数映射
```

| 方法 | 签名 | 说明 |
|------|------|------|
| `bind()` | `bind(ctx: Ctx) -> BoundTool[Ctx]` | 绑定上下文，返回可执行的 BoundTool |
| `resolve_deps()` | `resolve_deps(ctx: Ctx) -> dict[str, Any]` | 解析所有依赖参数，返回 {参数名: 值} |

### BoundTool

绑定了上下文的 Tool，可直接调用。

```python
@attrs.define(slots=True)
class BoundTool(Generic[Ctx]):
    _tool: Tool[Ctx]   # 原始 Tool
    _ctx: Ctx           # 绑定的上下文
```

### run() 方法

```python
async def run(self, *args: Any, **kwargs: Any) -> Any
```

**执行流程：**

1. 调用 `self._tool.resolve_deps(self._ctx)` 解析依赖
2. 合并依赖值与 kwargs：`merged = {**resolved, **kwargs}`（kwargs 可覆盖依赖）
3. 调用底层函数：`result = self._tool.fn(*args, **merged)`
4. 若结果是 awaitable → `await result`
5. 返回结果

**注意：** 同时支持同步和异步函数，自动检测并 await。

## ToolSpec & ParamSpec

### ParamSpec

```python
class ParamSpec(msgspec.Struct, frozen=True):
    name: str                    # 参数名
    type_schema: dict[str, Any]  # JSON Schema 片段
    description: str             # 参数描述
    required: bool = True        # 是否必填
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | `str` | 必填 | 参数名称 |
| `type_schema` | `dict` | 必填 | JSON Schema 类型片段，如 `{"type": "string"}` |
| `description` | `str` | 必填 | 参数描述（来自 `@tool(params=...)` 中的映射） |
| `required` | `bool` | `True` | 无默认值的参数为 True |

### ToolSpec

```python
class ToolSpec(msgspec.Struct, frozen=True):
    name: str                      # 工具名称
    description: str               # 工具描述
    params: tuple[ParamSpec, ...]  # 参数规格元组
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 工具名称 |
| `description` | `str` | 工具描述 |
| `params` | `tuple[ParamSpec, ...]` | 参数规格（不含依赖注入参数） |

### to() 方法

```python
def to(self, fmt: str) -> str
```

| 参数 | 说明 |
|------|------|
| `"json_schema"` | 输出 JSON 字符串（OpenAI function calling 格式） |
| `"yaml"` | 输出 YAML 字符串（需安装 pyyaml） |

**`to("json_schema")` 输出格式：**

```json
{
  "type": "function",
  "function": {
    "name": "tool_name",
    "description": "工具描述",
    "parameters": {
      "type": "object",
      "properties": {
        "param1": {
          "type": "string",
          "description": "参数1描述"
        },
        "param2": {
          "type": "integer",
          "description": "参数2描述"
        }
      },
      "required": ["param1"]
    }
  }
}
```

**注意：** `"required"` 数组仅在存在必填参数时出现。

## 类型转换 (`type_to_json_schema`)

```python
def type_to_json_schema(tp: Any) -> dict[str, Any]
```

将 Python 类型注解转为 JSON Schema 片段。

### 支持的类型映射

| Python 类型 | JSON Schema |
|------------|-------------|
| `str` | `{"type": "string"}` |
| `int` | `{"type": "integer"}` |
| `float` | `{"type": "number"}` |
| `bool` | `{"type": "boolean"}` |
| `None` / `NoneType` | `{"type": "null"}` |
| `list[X]` | `{"type": "array", "items": schema(X)}` |
| `list`（无参数） | `{"type": "array"}` |
| `dict[str, V]` | `{"type": "object", "additionalProperties": schema(V)}` |
| `dict`（无参数） | `{"type": "object"}` |
| `tuple[X, Y]` | `{"type": "array", "prefixItems": [schema(X), schema(Y)], "minItems": 2, "maxItems": 2}` |
| `tuple`（无参数） | `{"type": "array"}` |
| `Optional[X]` / `X \| None` | `{"anyOf": [schema(X), {"type": "null"}]}` |
| `Union[X, Y]` | `{"anyOf": [schema(X), schema(Y)]}` |
| `Literal["a", "b", 1]` | `{"enum": ["a", "b", 1]}` |
| 其他未识别类型 | `{"type": "string"}`（兜底） |

### 示例

```python
from yuutools._schema import type_to_json_schema

type_to_json_schema(str)
# {"type": "string"}

type_to_json_schema(list[int])
# {"type": "array", "items": {"type": "integer"}}

type_to_json_schema(dict[str, float])
# {"type": "object", "additionalProperties": {"type": "number"}}

type_to_json_schema(int | None)
# {"anyOf": [{"type": "integer"}, {"type": "null"}]}

from typing import Literal
type_to_json_schema(Literal["json", "yaml"])
# {"enum": ["json", "yaml"]}
```
