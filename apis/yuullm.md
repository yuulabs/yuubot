# yuullm API 文档

## 概述

yuullm 是一个统一的 LLM 流式调用客户端库。它为不同 LLM 供应商（OpenAI、Anthropic 等）提供标准化的消息格式和流式响应模型，使调用方无需关心底层 API 差异。

核心设计：
- **统一消息格式** — `Message = tuple[str, list[Item]]`，轻量元组而非重类
- **流式输出** — `AsyncIterator[StreamItem]` + `Store` 字典
- **Provider 可插拔** — 通过 Protocol 定义接口，内置 OpenAI / Anthropic 实现
- **成本追踪** — 三级定价层级自动计算调用费用

## 安装 & 快速开始

```python
import yuullm
from yuullm.providers import OpenAIChatCompletionProvider

# 1. 创建 Provider
provider = OpenAIChatCompletionProvider(api_key="sk-...")

# 2. 创建 Client
client = yuullm.YLLMClient(
    provider=provider,
    default_model="gpt-4o",
)

# 3. 构造消息
messages = [
    yuullm.system("你是一个有用的助手。"),
    yuullm.user("2 + 2 等于几？"),
]

# 4. 流式调用
stream, store = await client.stream(messages)
async for item in stream:
    match item:
        case yuullm.Response(item=text):
            print(text, end="")
        case yuullm.ToolCall() as tc:
            print(f"调用工具: {tc.name}({tc.arguments})")
        case yuullm.Reasoning(item=text):
            print(f"[思考] {text}")

# 5. 流消耗完毕后，store 中包含用量和费用
print(store["usage"])   # Usage(...)
print(store["cost"])    # Cost(...) 或 None
```

## 核心类型

### Message & History

```python
Message = tuple[str, list[Item]]
# (role, items)
# role: "system" | "user" | "assistant" | "tool"

History = list[Message]
```

`Message` 是一个轻量元组，第一个元素是角色字符串，第二个元素是内容项列表。

### 便捷构造函数

| 函数 | 签名 | 说明 |
|------|------|------|
| `system()` | `system(content: str) -> Message` | 创建系统消息 |
| `user()` | `user(*items: Item) -> Message` | 创建用户消息，支持多模态 |
| `assistant()` | `assistant(*items: Item) -> Message` | 创建助手消息 |
| `tool()` | `tool(tool_call_id: str, content: str) -> Message` | 创建工具结果消息 |

**示例：**

```python
import yuullm

# 纯文本
msg = yuullm.system("你是助手。")
# -> ("system", ["你是助手。"])

# 多模态用户消息
msg = yuullm.user("这张图片是什么？", {
    "type": "image_url",
    "image_url": {"url": "https://example.com/cat.png"}
})
# -> ("user", ["这张图片是什么？", {"type": "image_url", ...}])

# 带工具调用的助手消息
msg = yuullm.assistant("让我搜索一下。", {
    "type": "tool_call",
    "id": "tc_1",
    "name": "search",
    "arguments": '{"q": "test"}',
})

# 工具结果
msg = yuullm.tool("tc_1", "搜索到 5 条结果。")
# -> ("tool", [{"type": "tool_result", "tool_call_id": "tc_1", "content": "搜索到 5 条结果。"}])
```

### 内容项类型 (Item)

```python
Item = str | DictItem
DictItem = ToolCallItem | ToolResultItem | TextItem | ImageItem | AudioItem | FileItem
```

`Item` 可以是纯字符串或结构化的 TypedDict。

#### TextItem

```python
class TextItem(TypedDict):
    type: Literal["text"]
    text: str
```

#### ImageItem

```python
class ImageItem(TypedDict):
    type: Literal["image_url"]
    image_url: _ImageURL  # {"url": str, "detail"?: "auto" | "low" | "high"}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `Literal["image_url"]` | 是 | 固定值 |
| `image_url.url` | `str` | 是 | 图片 URL 或 base64 data URI |
| `image_url.detail` | `"auto" \| "low" \| "high"` | 否 | 分辨率控制 |

#### AudioItem

```python
class AudioItem(TypedDict):
    type: Literal["input_audio"]
    input_audio: _InputAudio  # {"data": str, "format": "wav" | "mp3"}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `Literal["input_audio"]` | 是 | 固定值 |
| `input_audio.data` | `str` | 是 | base64 编码音频 |
| `input_audio.format` | `"wav" \| "mp3"` | 是 | 音频格式 |

#### FileItem

```python
class FileItem(TypedDict):
    type: Literal["file"]
    file: _FileData  # {"file_data"?: str, "file_id"?: str, "filename"?: str}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `Literal["file"]` | 是 | 固定值 |
| `file.file_data` | `str` | 否 | base64 编码文件数据 |
| `file.file_id` | `str` | 否 | 文件 ID（API 上传后获得） |
| `file.filename` | `str` | 否 | 文件名 |

#### ToolCallItem

```python
class ToolCallItem(TypedDict):
    type: Literal["tool_call"]
    id: str
    name: str
    arguments: str  # 原始 JSON 字符串
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `Literal["tool_call"]` | 固定值 |
| `id` | `str` | 工具调用 ID |
| `name` | `str` | 工具名称 |
| `arguments` | `str` | 参数 JSON 字符串（未解析） |

#### ToolResultItem

```python
class ToolResultItem(TypedDict):
    type: Literal["tool_result"]
    tool_call_id: str
    content: str
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `Literal["tool_result"]` | 固定值 |
| `tool_call_id` | `str` | 对应的工具调用 ID |
| `content` | `str` | 工具执行结果 |

## YLLMClient

统一 LLM 客户端，封装 Provider 和可选的 PriceCalculator。

### 构造函数

```python
class YLLMClient:
    def __init__(
        self,
        provider: Provider,
        default_model: str,
        tools: list[dict[str, Any]] | None = None,
        price_calculator: PriceCalculator | None = None,
    ) -> None
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `provider` | `Provider` | 是 | - | Provider 实例 |
| `default_model` | `str` | 是 | - | 默认模型标识符 |
| `tools` | `list[dict] \| None` | 否 | `None` | 默认工具列表（json_schema 格式的 dict） |
| `price_calculator` | `PriceCalculator \| None` | 否 | `None` | 成本计算器 |

### stream() 方法

```python
async def stream(
    self,
    messages: list[Message],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    **kwargs,
) -> StreamResult
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `messages` | `list[Message]` | 是 | - | 会话历史 |
| `model` | `str \| None` | 否 | `None` | 覆盖默认模型（None 时用 default_model） |
| `tools` | `list[dict] \| None` | 否 | `None` | 覆盖默认工具（None 时用构造函数传入的 tools） |
| `**kwargs` | - | - | - | 透传给 provider.stream() |

**返回值：** `StreamResult = tuple[AsyncIterator[StreamItem], Store]`

- 迭代器产出 `Reasoning`、`ToolCall`、`Response` 对象
- 迭代器耗尽后，`Store` 字典包含：
  - `"usage"` → `Usage` 对象
  - `"cost"` → `Cost | None`

## 流式输出项 StreamItem

```python
StreamItem = Reasoning | ToolCall | Response
```

三种输出类型均为 `msgspec.Struct(frozen=True)` 不可变对象。

### Reasoning

```python
class Reasoning(msgspec.Struct, frozen=True):
    item: Item  # 思维链/扩展思考片段
```

模型的推理过程片段（如 Claude 的 extended thinking、DeepSeek 的 reasoning_content）。

### ToolCall

```python
class ToolCall(msgspec.Struct, frozen=True):
    id: str         # 工具调用 ID
    name: str       # 工具名称
    arguments: str  # 参数 JSON 字符串
```

模型请求调用工具。

### Response

```python
class Response(msgspec.Struct, frozen=True):
    item: Item  # 回复片段（通常为 str）
```

模型的最终回复片段。

### Store 字典

```python
Store = dict
StreamResult = tuple[AsyncIterator[StreamItem], Store]
```

`Store` 是一个可变字典，在迭代器耗尽后填充以下键：

| 键 | 类型 | 说明 |
|----|------|------|
| `"usage"` | `Usage` | Token 用量 |
| `"cost"` | `Cost \| None` | 费用（无法计算时为 None） |

## Provider 协议

```python
class Provider(Protocol):
    @property
    def api_type(self) -> str: ...

    @property
    def provider(self) -> str: ...

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        **kwargs,
    ) -> StreamResult: ...
```

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `api_type` | `str` | 线路协议标识，如 `"openai-chat-completion"`, `"anthropic-messages"` |
| `provider` | `str` | 供应商名称，如 `"openai"`, `"deepseek"`, `"anthropic"` |

### stream() 方法

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `messages` | `list[Message]` | 是 | 会话历史 |
| `model` | `str` | 是 | 模型标识符 |
| `tools` | `list[dict] \| None` | 否 | 工具定义（json_schema dict） |
| `**kwargs` | - | - | 供应商特定参数 |

**返回值：** `StreamResult`

Store 必须填充 `"usage"` (Usage)，可选填充 `"provider_cost"` (float | None)。

## 内置 Provider

### OpenAIChatCompletionProvider

兼容 OpenAI Chat Completion API (`/v1/chat/completions`) 及所有兼容端点（DeepSeek、OpenRouter、Together、Groq 等）。

```python
from yuullm.providers import OpenAIChatCompletionProvider

provider = OpenAIChatCompletionProvider(
    api_key="sk-...",              # 可选，默认取 OPENAI_API_KEY 环境变量
    base_url="https://...",        # 可选，第三方端点覆盖
    organization="org-...",        # 可选，OpenAI 组织 ID
    provider_name="openai",        # keyword-only，供应商名称，影响 Usage.provider 和定价查找
)
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `api_key` | `str \| None` | 否 | `None` | API 密钥，None 时取 `OPENAI_API_KEY` 环境变量 |
| `base_url` | `str \| None` | 否 | `None` | 自定义 API 端点 |
| `organization` | `str \| None` | 否 | `None` | OpenAI 组织 ID |
| `provider_name` | `str` | 否 | `"openai"` | 供应商标识（keyword-only） |

**属性：**
- `api_type` → `"openai-chat-completion"`
- `provider` → `provider_name` 的值

**第三方端点示例：**

```python
# DeepSeek
provider = OpenAIChatCompletionProvider(
    api_key="sk-...",
    base_url="https://api.deepseek.com",
    provider_name="deepseek",
)

# OpenRouter
provider = OpenAIChatCompletionProvider(
    api_key="sk-...",
    base_url="https://openrouter.ai/api/v1",
    provider_name="openrouter",
)
```

**推理内容处理：**
- `reasoning_content` 字段 → DeepSeek 格式
- `reasoning` 字段 → OpenRouter 格式
- 两种格式均自动识别并产出 `Reasoning` 对象

### AnthropicMessagesProvider

Anthropic Messages API (`/v1/messages`)。

```python
from yuullm.providers import AnthropicMessagesProvider

provider = AnthropicMessagesProvider(
    api_key="sk-ant-...",          # 可选，默认取 ANTHROPIC_API_KEY 环境变量
    base_url="https://...",        # 可选，代理端点
    provider_name="anthropic",     # keyword-only，供应商名称
)
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `api_key` | `str \| None` | 否 | `None` | API 密钥，None 时取 `ANTHROPIC_API_KEY` 环境变量 |
| `base_url` | `str \| None` | 否 | `None` | 自定义 API 端点 |
| `provider_name` | `str` | 否 | `"anthropic"` | 供应商标识（keyword-only） |

**属性：**
- `api_type` → `"anthropic-messages"`
- `provider` → `provider_name` 的值

**注意事项：**
- 系统消息会被自动分离并作为顶层 `system` 参数传递（Anthropic API 要求）
- 工具调用的 `arguments` JSON 字符串会被自动解析为对象
- 工具结果嵌入在 `user` 角色消息中的 `tool_result` 内容块
- 默认 `max_tokens=8192`（若 kwargs 未指定）
- 缓存 token 从 `cache_read_input_tokens` / `cache_creation_input_tokens` 提取

### 废弃别名

```python
# 这两个别名已废弃，请使用完整名称
OpenAIProvider = OpenAIChatCompletionProvider       # 废弃
AnthropicProvider = AnthropicMessagesProvider         # 废弃
```

## PriceCalculator

三级定价层级自动计算 LLM 调用费用。

### 定价层级（优先级从高到低）

1. **Provider 直报** — 某些供应商（如 OpenRouter）直接返回费用
2. **YAML 配置** — 用户在 YAML 文件中自定义定价
3. **genai-prices 库** — 开源库作为兜底

### 构造函数

```python
class PriceCalculator:
    def __init__(
        self,
        yaml_path: str | Path | None = None,
        enable_genai_prices: bool = True,
    ) -> None
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `yaml_path` | `str \| Path \| None` | 否 | `None` | YAML 定价文件路径 |
| `enable_genai_prices` | `bool` | 否 | `True` | 是否启用 genai-prices 库兜底 |

### YAML 定价格式

```yaml
- provider: "openai"
  models:
    - id: "gpt-4o"
      prices:
        input_mtok: 2.50      # 美元 / 百万输入 token
        output_mtok: 10.00    # 美元 / 百万输出 token
        cache_read_mtok: 1.25
        cache_write_mtok: 2.50
- provider: "anthropic"
  models:
    - id: "claude-sonnet-4-20250514"
      prices:
        input_mtok: 3.00
        output_mtok: 15.00
```

### calculate() 方法

```python
def calculate(
    self,
    usage: Usage,
    provider_cost: float | None = None,
) -> Cost | None
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `usage` | `Usage` | 是 | - | Token 用量数据 |
| `provider_cost` | `float \| None` | 否 | `None` | Provider 直报的费用（最高优先级） |

**返回值：** `Cost | None`

**计算公式（YAML 和 genai-prices 层级）：**

```
input_cost     = input_tokens     × input_mtok     / 1_000_000
output_cost    = output_tokens    × output_mtok    / 1_000_000
cache_read_cost  = cache_read_tokens  × cache_read_mtok  / 1_000_000
cache_write_cost = cache_write_tokens × cache_write_mtok / 1_000_000
total_cost     = input_cost + output_cost + cache_read_cost + cache_write_cost
```

## Usage & Cost

### Usage

```python
class Usage(msgspec.Struct, frozen=True):
    provider: str
    model: str
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int | None = None
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `provider` | `str` | 必填 | 供应商标识（如 `"openai"`, `"anthropic"`） |
| `model` | `str` | 必填 | 模型标识符 |
| `request_id` | `str \| None` | `None` | API 请求 ID |
| `input_tokens` | `int` | `0` | 输入 token 数 |
| `output_tokens` | `int` | `0` | 输出 token 数 |
| `cache_read_tokens` | `int` | `0` | 缓存读取 token 数 |
| `cache_write_tokens` | `int` | `0` | 缓存写入 token 数 |
| `total_tokens` | `int \| None` | `None` | 总 token 数 |

### Cost

```python
class Cost(msgspec.Struct, frozen=True):
    input_cost: float
    output_cost: float
    total_cost: float
    cache_read_cost: float = 0.0
    cache_write_cost: float = 0.0
    source: str = ""
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `input_cost` | `float` | 必填 | 输入 token 费用（USD） |
| `output_cost` | `float` | 必填 | 输出 token 费用（USD） |
| `total_cost` | `float` | 必填 | 总费用（USD） |
| `cache_read_cost` | `float` | `0.0` | 缓存读取费用（USD） |
| `cache_write_cost` | `float` | `0.0` | 缓存写入费用（USD） |
| `source` | `str` | `""` | 定价来源：`"provider"` / `"yaml"` / `"genai-prices"` |
