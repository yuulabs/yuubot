# yuutrace Design

## Overview

yuutrace 是一个面向 LLM 工作负载的可观测性 SDK，构建在 OpenTelemetry 之上。它提供：

1. **Python SDK** -- 结构化的 API 用于记录对话、LLM 调用、工具调用的 span/event，以及计费与用量数据
2. **React 组件库 (`@yuutrace/ui`)** -- 可复用的 React 组件，用于 trace 可视化（瀑布对话流、气泡卡片、费用/用量面板等）
3. **CLI 工具** -- 启动 OTEL Collector + 存储后端（默认 SQLite）+ 一个使用 `@yuutrace/ui` 构建的独立 WebUI 页面

yuutrace 遵循 `ytrace_spec.md` 中定义的 OTEL 数据形态规范。

## Architecture: Two Deliverables

yuutrace 由两个独立的发布物组成：

| Deliverable | 类型 | 用途 |
| --- | --- | --- |
| `yuutrace` (PyPI) | Python 包 | SDK 埋点 + CLI（server / ui） |
| `@yuutrace/ui` (npm) | React 组件库 | trace 可视化组件，供 yuutrace CLI 及其他项目（如 yuuagents dashboard）复用 |

### 依赖关系

```
yuuagents dashboard ──imports──> @yuutrace/ui (React components)
yuutrace CLI (ui)   ──imports──> @yuutrace/ui (React components)
yuuagents (Python)  ──imports──> yuutrace (Python SDK, for instrumentation)
```

这种设计使得：
- yuutrace CLI 的 WebUI 只是 `@yuutrace/ui` 的一个**薄壳页面**（组装组件 + 连接数据源）
- yuuagents dashboard 可以直接 import `@yuutrace/ui` 中的组件，在自己的 dashboard 中嵌入 trace 可视化，而无需重复实现

## Key Concepts

### Span 层级（推荐）

```
conversation span (root)
  ├── llm span        (一次模型请求)
  ├── tool span       (一次工具调用)
  ├── llm span
  └── ...
```

### Current Span & Fast Fail

`current_span()` 返回当前活动 span。若无活动 span 则直接抛异常（快速失败），不隐式创建。

### Delta 语义

所有 cost / usage 数据都是增量（delta）。同一个 span 可以写入多条 event。聚合在查询层完成。

### Event 命名

| Event Name        | 用途         |
| ----------------- | ------------ |
| `yuu.cost`        | 计费增量     |
| `yuu.llm.usage`   | LLM token 用量 |
| `yuu.tool.usage`  | 工具用量（可选） |

业务侧不直接写 event name / attribute key；一律通过包装接口。

## Python SDK: Module Layout

```
src/yuutrace/
    __init__.py          # public API re-exports
    py.typed
    types.py             # CostDelta, LlmUsageDelta, ToolUsageDelta, CostCategory, Currency
    span.py              # current_span(), add_event() -- 底层接口
    cost.py              # record_cost_delta(), record_cost() -- 计费包装
    usage.py             # record_llm_usage(), record_tool_usage() -- 用量包装
    context.py           # conversation(), llm_gen(), tools() -- 上下文管理器
    otel.py              # OTEL attribute 序列化辅助 (to_otel_attributes)
    cli/
        __init__.py
        main.py          # CLI 入口 (ytrace server / ytrace ui)
        server.py        # OTEL Collector + SQLite 存储
        ui.py            # 构建并 serve @yuutrace/ui 的独立页面
```

## React 组件库: `@yuutrace/ui`

### 设计原则

1. **纯展示组件** -- 组件只负责渲染，不绑定具体数据获取方式。数据通过 props 或 React context 注入。
2. **可组合** -- 每个组件独立可用，也可以组合成完整页面。yuutrace CLI 组装一个完整页面；yuuagents dashboard 只挑选需要的组件嵌入。
3. **数据契约来自 ytrace_spec** -- 组件的 TypeScript 类型定义与 `ytrace_spec.md` 中的 OTEL 数据形态对齐。

### Package Layout

```
ui/
    package.json             # @yuutrace/ui
    tsconfig.json
    src/
        index.ts             # public exports
        types.ts             # TypeScript 类型 (mirrors ytrace_spec.md)
        components/
            ConversationList.tsx    # 对话列表 (按 conversation.id 分组)
            ConversationFlow.tsx    # 瀑布对话流 (span 时间线)
            LlmCard.tsx             # LLM 调用卡片 (provider/model/tokens/cost)
            ToolCard.tsx            # 工具调用卡片 (name/usage/cost)
            CostSummary.tsx         # 费用汇总面板
            UsageSummary.tsx        # 用量汇总面板
            SpanTimeline.tsx        # Span 时间线视图
        hooks/
            useTraceData.ts         # 数据获取 hook (可选，面向 yuutrace CLI)
        pages/
            TracePage.tsx           # 完整 trace 页面 (yuutrace CLI 使用)
```

### 核心组件

| 组件 | 用途 | 数据 Props |
| --- | --- | --- |
| `ConversationList` | 对话列表，支持搜索和筛选 | `conversations: Conversation[]` |
| `ConversationFlow` | 单个对话的瀑布式 span 时间线 | `spans: Span[]`, `events: Event[]` |
| `LlmCard` | LLM 调用详情卡片 | `usage: LlmUsage`, `cost?: Cost` |
| `ToolCard` | 工具调用详情卡片 | `name: string`, `usage?: ToolUsage`, `cost?: Cost` |
| `CostSummary` | 费用汇总（按 category/model 分组） | `costs: CostEvent[]` |
| `UsageSummary` | 用量汇总（token 用量统计） | `usages: LlmUsageEvent[]` |
| `SpanTimeline` | 通用 span 时间线 | `spans: Span[]` |

### TypeScript 类型 (与 ytrace_spec 对齐)

```typescript
// mirrors ytrace_spec.md OTEL data shape

interface Conversation {
  id: string;
  agent: string;
  tags?: string[];
  spans: Span[];
}

interface Span {
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  name: string;
  startTime: number;
  endTime: number;
  attributes: Record<string, unknown>;
  events: SpanEvent[];
}

interface SpanEvent {
  name: string;          // "yuu.cost" | "yuu.llm.usage" | "yuu.tool.usage"
  timestamp: number;
  attributes: Record<string, unknown>;
}

// Parsed event types
interface CostEvent {
  category: "llm" | "tool";
  currency: string;
  amount: number;
  source?: string;
  pricingId?: string;
  llmProvider?: string;
  llmModel?: string;
  llmRequestId?: string;
  toolName?: string;
  toolCallId?: string;
}

interface LlmUsageEvent {
  provider: string;
  model: string;
  requestId?: string;
  inputTokens: number;
  outputTokens: number;
  totalTokens?: number;
}

interface ToolUsageEvent {
  name: string;
  callId?: string;
  unit: string;
  quantity: number;
}
```

### 复用示例: yuuagents dashboard

```tsx
// yuuagents dashboard 中嵌入 trace 可视化
import { ConversationFlow, CostSummary, LlmCard } from "@yuutrace/ui";

function AgentDetailPanel({ agentTraceData }) {
  return (
    <div>
      <h2>Agent Trace</h2>
      <CostSummary costs={agentTraceData.costs} />
      <ConversationFlow
        spans={agentTraceData.spans}
        events={agentTraceData.events}
      />
    </div>
  );
}
```

## Key Types (Python SDK)

```python
import msgspec
from enum import Enum

class CostCategory(str, Enum):
    llm = "llm"
    tool = "tool"

class Currency(str, Enum):
    USD = "USD"

class CostDelta(msgspec.Struct, frozen=True):
    category: CostCategory
    currency: Currency
    amount: float
    source: str | None = None
    pricing_id: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_request_id: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None

class LlmUsageDelta(msgspec.Struct, frozen=True):
    provider: str
    model: str
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int | None = None

class ToolUsageDelta(msgspec.Struct, frozen=True):
    name: str
    call_id: str | None = None
    unit: str
    quantity: float
```

## Public API (Python SDK)

### Low-level

```python
def current_span() -> Span: ...
def add_event(name: str, attributes: dict[str, object]) -> None: ...
```

### Wrappers (recommended)

```python
# Cost
def record_cost_delta(cost: CostDelta) -> None: ...
def record_cost(*, category, currency, amount, ...) -> None: ...

# LLM usage
def record_llm_usage(usage: LlmUsageDelta) -> None: ...
def record_llm_usage(*, provider, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, ...) -> None: ...

# Tool usage (optional)
def record_tool_usage(usage: ToolUsageDelta) -> None: ...
```

### Context Managers

```python
from uuid import UUID

def conversation(
    *,
    id: UUID,
    agent: str,
    model: str,
    tags: dict | None = None,
) -> ContextManager[ConversationContext]: ...

class ConversationContext:
    def system(self, persona: str, tools: list | None = None) -> None: ...
    def user(self, content: str) -> None: ...
    def llm_gen(self) -> ContextManager[LlmGenContext]: ...
    def tools(self) -> ContextManager[ToolsContext]: ...

class LlmGenContext:
    def log(self, items: list) -> None: ...

class ToolsContext:
    async def gather(self, calls: list[dict]) -> list[ToolResult]: ...
```

## Example Usage (Python SDK)

```python
import yuutrace as ytrace
from uuid import uuid4

with ytrace.conversation(id=uuid4(), agent="my-agent", model="gpt-4o") as chat:
    chat.system(persona="You are helpful.", tools=tool_specs)
    chat.user("What is Bitcoin price?")

    with chat.llm_gen() as gen:
        stream, store = await llm.stream(messages)
        items = [item async for item in stream]
        gen.log(items)

        usage = store["usage"]
        ytrace.record_llm_usage(
            provider=usage.provider,
            model=usage.model,
            request_id=usage.request_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            total_tokens=usage.total_tokens,
        )

        cost = store["cost"]
        if cost is not None:
            ytrace.record_cost(
                category="llm",
                currency="USD",
                amount=cost.total_cost,
                source=cost.source,
            )

    with chat.tools() as tools:
        results = await tools.gather([
            {"tool_call_id": "call_1", "tool": my_fn, "params": {"q": "BTC"}},
        ])
```

## CLI

```bash
# Start OTEL collector + SQLite storage
ytrace server --db ./traces.db --port 4318

# Start WebUI (serves the standalone TracePage from @yuutrace/ui)
ytrace ui --port 8080 --db ./traces.db
```

`ytrace ui` 的实现：
- Python 侧启动 HTTP 服务器，提供 REST API 从 SQLite 查询 trace 数据
- 同时 serve `@yuutrace/ui` 中 `TracePage` 的预构建静态产物
- 前端通过 API 获取数据并渲染组件

## Design Decisions

1. **OTEL-native** -- 所有数据最终以标准 OTEL spans/events 形式存储，兼容任何 OTEL-compatible 前端。
2. **业务侧零 magic strings** -- event name / attribute key 全部封装在 ytrace 内部，业务侧只调用类型安全的函数。
3. **Delta-only** -- 不做累加，聚合交给查询层。简化并发写入场景。
4. **Fast-fail on missing span** -- 不隐式创建 span，避免静默丢数据。
5. **msgspec structs** -- 与 yuullm / yuuagents 保持一致的序列化方案。
6. **React 组件库独立发布** -- `@yuutrace/ui` 作为独立 npm 包，使 yuuagents 等下游项目可以直接 import 组件，避免前端代码重复。
7. **组件纯展示、数据注入** -- 组件不绑定数据源，通过 props/context 注入。不同宿主（ytrace CLI、yagents dashboard）各自负责数据获取。
8. **CLI 是薄壳** -- `ytrace ui` 只做两件事：(1) 提供 REST API 查询 SQLite；(2) serve 预构建的前端静态资源。不包含前端构建逻辑。
