# Design: Observability — Tracing, LLM Timing, and Cost Aggregation

**实现顺序：9**（依赖 [01-runtime-events.md](01-runtime-events.md)、[02-admin-boundary.md](02-admin-boundary.md)、[08-python-kernel.md](08-python-kernel.md)）

## Scenario

两类观测：

| 类型 | 问题 | 存储 | 消费 |
| --- | --- | --- | --- |
| **Cost / usage** | 花费、token、tool 分布 | `app_costs`, `history` | Monitor Cost Dashboard |
| **Trace / timing** | 慢路径、TTFT、overthink、嵌套子链路 | `app_spans` | Monitor 聚合；深度下钻经 Actor + `profiling` skill |

```text
【对话】invoke_agent amy → chat gpt-4o#0（TTFT 0.8s；first_text @2.4s）
  → execute_tool read → chat gpt-4o#1 → epilogue → app_costs + app_spans 异步落盘

【Monitor】GET /api/usage/* 读 SQLite 聚合（summary / latency / phases / tools）

【深度分析】用户提问 → Actor 加载 profiling skill → execute_python 内 yb.observe 查库
  → artifacts/*.png 或 projects/*.html
```

**OTEL GenAI Semconv**：实现 pin **v1.41.0**（[gen-ai-spans](https://github.com/open-telemetry/semantic-conventions/blob/v1.41.0/docs/gen-ai/gen-ai-spans.md)、[gen-ai-agent-spans](https://github.com/open-telemetry/semantic-conventions/blob/v1.41.0/docs/gen-ai/gen-ai-agent-spans.md)）。`attrs` 键带 `gen_ai.` 前缀；OTEL 未覆盖的扩展用 `yuubot.*`。

## Concepts

```text
TracerProvider       = 进程级 OTEL 入口；与 Runtime startup/shutdown 同生命周期
Tracer               = "io.yuulabs.yuubot"
SpanExporter         = 默认 SqliteSpanExporter → app_spans
BatchSpanProcessor   = 异步批量写库
yuubot.observe       = emit gen_ai.* attrs + span kind（写路径）
yb.observe           = execute_python 内只读 facade（读 app_spans / app_costs）
profiling skill      = .agents/skills/profiling/SKILL.md
```

**读路径**：

```text
daemon     → 写 app_spans / app_costs；GET /api/usage/*（Monitor）
ipykernel  → yb.observe 只读打开 YUUBOT_DB_PATH（mode=ro）；与 Admin 共享 yuubot.observe.queries
```

聚合 SQL 放在 `yuubot.observe.queries`；Admin 与 `yb.observe` import 同一函数。

## SDK

### `yuubot.observe`（写）

```py
from opentelemetry.trace import SpanKind

tracer = trace.get_tracer("io.yuulabs.yuubot", "0.1.0")

@contextlib.asynccontextmanager
async def gen_ai_span(
    operation: Literal["invoke_agent", "chat", "execute_tool"],
    *,
    name: str,
    kind: SpanKind,
    attrs: dict[str, object],     # gen_ai.* keys without prefix
) -> AsyncIterator[trace.Span]: ...

@contextlib.asynccontextmanager
async def span(name: str, *, kind: SpanKind = SpanKind.INTERNAL, **attrs: object) -> AsyncIterator[trace.Span]:
    """startup, admin.request, yuubot.* extensions."""

def record_stream_chunk(*, first_chunk: bool = False, first_text: bool = False, last_text: bool = False) -> None: ...
def set_chat_usage(stop: StreamStop) -> None: ...
def current_trace_id() -> str | None: ...
def current_span_id() -> str | None: ...
```

业务代码只调 `observe.gen_ai_span` / `observe.span`；exporter 层加 `gen_ai.` 前缀。

### `yb.observe`（只读，ipykernel）

| 方法 | 用途 |
| --- | --- |
| `spans(...)` | 过滤 `app_spans`：`trace_id`, `conversation_id`, `range`, `name_glob`, `slowest`, `max_rows` |
| `costs(...)` | 过滤 `app_costs`；字段对齐会话 cost API |
| `trace_tree(trace_id)` | 扁平列表（含 `parent_span_id`，上限 `max_spans`） |
| `summary(range)` | 同 `GET /api/usage/summary` |

## Span 模型

### 根 span

| 根 span 名 | kind | `gen_ai.operation.name` | 触发点 |
| --- | --- | --- | --- |
| `startup` | INTERNAL | — | `Runtime.create` |
| `invoke_agent {actor}` | INTERNAL | `invoke_agent` | `Conversation.run_loop` |
| `admin.request` | SERVER | — | Admin HTTP 写操作 |

嵌套：`execute_python` 内 task / inbound 产生 child span，共享父 `trace_id`（OTEL context 传播）。

### 映射

| yuubot 概念 | Span name | kind | 必填 `gen_ai.*` attrs |
| --- | --- | --- | --- |
| 一轮对话 | `invoke_agent {gen_ai.agent.name}` | INTERNAL | `operation.name=invoke_agent`, `provider.name=yuubot`, `conversation.id`, `agent.name` |
| provider.stream | `chat {gen_ai.request.model}` | CLIENT | `operation.name=chat`, `provider.name`, `request.model`, `request.stream=true` |
| tool 执行 | `execute_tool {gen_ai.tool.name}` | INTERNAL | `operation.name=execute_tool`, `tool.name`, `tool.call.id` |
| 冷启动 | `yuubot.conversation.bootstrap` | INTERNAL | `conversation.id` |
| tool merge | `yuubot.tool.plan` | INTERNAL | — |
| turn 收尾 | `yuubot.conversation.epilogue` | INTERNAL | `conversation.id` |

`gen_ai.agent.name` = actor id。`gen_ai.conversation.id` = yuubot `conversation_id`。

`chat` 的 `gen_ai.provider.name` 由 provider 记录推断（`api.openai.com` → `openai`，`api.anthropic.com` → `anthropic`，Azure → `azure.ai.openai`，其他 → 配置或 `_OTHER`）。

### Span 树（conversation turn）

```text
invoke_agent amy                         INTERNAL
├── yuubot.conversation.bootstrap
├── chat gpt-4o-mini                     CLIENT  [round_index 扩展 attr]
│   attrs @end: usage.*, response.finish_reasons, response.time_to_first_chunk
│   events: yuubot.stream.first_text, yuubot.stream.last_text
├── yuubot.tool.plan
├── execute_tool read                    INTERNAL × N
└── yuubot.conversation.epilogue
```

Startup：`startup` → `db.open`, `db.migrate`, `state.rebind`, `actors.start`, `integrations.start`。

Admin：`admin.request` → `db.write`（写操作且 >5ms 或 debug）。

### Streaming timing

| 信号 | 类型 | 触发 | 含义 |
| --- | --- | --- | --- |
| `gen_ai.response.time_to_first_chunk` | span attr（秒） | 首个任意 stream event | chunk 级 TTFT |
| `yuubot.stream.first_text` | span event | 首个 `text_delta` | 正文开始 |
| `yuubot.stream.last_text` | span event | 该 round 最后 `text_delta` | 正文结束 |

`reasoning_delta` 不计入 text。`record_stream_chunk(first_chunk=True)` 写入 `time_to_first_chunk`。

stream 结束后在 `chat` span 设置：`gen_ai.usage.*`, `gen_ai.response.finish_reasons`, `gen_ai.response.model`。

**派生指标（聚合 API）**：

| 指标 | 公式 |
| --- | --- |
| `time_to_first_chunk_ms` | `gen_ai.response.time_to_first_chunk` × 1000 |
| `time_to_first_text_ms` | `first_text` event − span start |
| `thinking_before_text_ms` | 同上（无 reasoning 时 ≈ time_to_first_text） |
| `text_generation_ms` | `last_text` − `first_text` |
| `overthink_ratio` | `thinking_before_text_ms` / chat duration |

**PhaseBreakdown**：

```text
thinking_time_ms       = sum(chat: first_text − span.start)
text_time_ms           = sum(chat: last_text − first_text)
tool_call_time_ms      = sum(yuubot.tool.plan duration)
tool_execution_time_ms = sum(execute_tool duration)
```

## Storage

### `app_spans`

```sql
create table app_spans (
    trace_id text not null,
    span_id text not null,
    parent_span_id text,
    name text not null,
    span_kind text not null default 'INTERNAL',
    started_at text not null,
    ended_at text not null,
    duration_ms integer not null,
    status text not null default 'ok',
    attrs blob not null default '{}',      -- gen_ai.* + yuubot.* + http.*
    events blob not null default '[]',
    conversation_id text,                  -- 冗余 gen_ai.conversation.id
    primary key (trace_id, span_id)
);

create index idx_app_spans_started_at on app_spans(started_at);
create index idx_app_spans_trace on app_spans(trace_id);
create index idx_app_spans_name on app_spans(name);
create index idx_app_spans_conversation on app_spans(conversation_id);
create index idx_app_spans_operation on app_spans(json_extract(attrs, '$.gen_ai.operation.name'));
```

`events`：`[{ "name": "yuubot.stream.first_text", "ts": "ISO8601", "attrs": {} }]`。

### `app_costs` 扩展

```text
trace_id     text
span_id      text      -- 对应 chat span
round_index  integer   -- yuubot.round_index
```

权威 token/USD 仍在 `usage` blob（与 `gen_ai.usage.*` 对齐）。`append_cost` 入库前 `Usage` 须含最终 `payg_cost`。

## HTTP API

`USAGE_BASE` → `/api/usage`（Monitor 浏览器）。认证同 [02-admin-boundary.md](02-admin-boundary.md)。

### `GET /api/usage/summary?range={day|week|month|year|total}`

数据源：`app_costs`。

```json
{
  "cost": 0.100299,
  "requests": 19,
  "input_tokens_uncached": 60622,
  "cached_input_tokens": 8960,
  "output_tokens": 2341
}
```

### `GET /api/usage/tools?range=...`

数据源：`history` where `kind = 'gen_tool_call'`，`json_extract(payload, '$.name')`。

```json
[{ "tool_name": "read", "count": 8 }]
```

### `GET /api/usage/latency?range=...`

数据源：`app_spans` where `gen_ai.operation.name = 'chat'`。

```json
{
  "avg_first_token_latency_ms": 812.5,
  "avg_turn_time_ms": 4520.0,
  "avg_tool_execution_time_ms": 48.2,
  "tool_execution_samples": 10,
  "avg_time_to_first_text_ms": 2100.0,
  "avg_thinking_before_text_ms": 1650.0
}
```

无数据时返回零值。

### `GET /api/usage/phases?range=...`

`range=year|total` 返回 400 或 `null`（与前端 `usePhaseBreakdown` 一致）。

```json
{
  "thinking_time_ms": 12000,
  "text_time_ms": 8000,
  "tool_call_time_ms": 2000,
  "tool_execution_time_ms": 5000
}
```

### 聚合类型

```py
class UsageSummary(msgspec.Struct):
    cost: float
    requests: int
    input_tokens_uncached: int
    cached_input_tokens: int
    output_tokens: int

class UsageLatency(msgspec.Struct):
    avg_first_token_latency_ms: float
    avg_turn_time_ms: float
    avg_tool_execution_time_ms: float
    tool_execution_samples: int
    avg_time_to_first_text_ms: float
    avg_thinking_before_text_ms: float

class ToolCallCount(msgspec.Struct):
    tool_name: str
    count: int

class PhaseBreakdown(msgspec.Struct):
    thinking_time_ms: float
    text_time_ms: float
    tool_call_time_ms: float
    tool_execution_time_ms: float
```

## Flow

```text
Runtime startup（listeners.start 之前）
  → TracerProvider + BatchSpanProcessor(SqliteSpanExporter)
Runtime shutdown（listeners.stop 之后）
  → provider.shutdown()

run_loop
  → gen_ai_span("invoke_agent") → bootstrap → loop:
      gen_ai_span("chat") → provider.stream → record_stream_chunk → set_chat_usage
      → _record_cost(trace_id, span_id, round_index)
      → yuubot.tool.plan → parallel execute_tool → round_index++
  → epilogue

_record_cost → append_cost(..., trace_id, span_id) + emit conversation.cost（可选，含 trace_id）

execute_python 入口：daemon 将 W3C traceparent 注入 kernel env；facade 进程 TracerProvider 见 [08-python-kernel.md](08-python-kernel.md) OTEL 预留。

Admin POST|PUT|DELETE → middleware span("admin.request")；GET 默认不插桩

Exporter 失败：drop + log。BatchSpanProcessor 不阻塞 hot path。
```

**Runtime 组合**：`observe: ObserveRuntime`（TracerProvider + shutdown hook）。

**依赖包**：`opentelemetry-api`, `opentelemetry-sdk`。可选：`opentelemetry-instrumentation-httpx`，`opentelemetry-exporter-otlp-proto-http`。

## Implementation

| Phase | 内容 | 验收 |
| --- | --- | --- |
| **A** | `/api/usage/summary`, `/tools`；`_record_cost` 统一 `with_payg` | Monitor summary 非零 |
| **B** | `yuubot.observe` + `app_spans` migration + `ObserveRuntime` | `app_spans` 可查 |
| **C** | conversation 插桩 + streaming timing + `/latency`, `/phases` | latency / phase 非零 |
| **D** | startup / admin spans + `yb.observe` + profiling skill | Actor 可出 profile 产物 |
| **E** | OTLP export、`observe.sample_rate`、httpx 子 span | 可选 |

## Related

- [01-runtime-events.md](01-runtime-events.md)、[02-admin-boundary.md](02-admin-boundary.md)
- [`src/yuubot/chat/loop.py`](../../src/yuubot/chat/loop.py)、[`src/yuubot/runtime/store.py`](../../src/yuubot/runtime/store.py)
- Monitor UI：[`web/src/features/monitor/`](../../web/src/features/monitor/)
- ISSUE-0009：[`roadmap/issues/ISSUE-0009-monitor-llm-timing-bug.md`](../../roadmap/issues/ISSUE-0009-monitor-llm-timing-bug.md)
