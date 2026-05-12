# TODO: Trace / Cost 后端接线关键风险

## 背景

当前 v2 后端已经有 `trace` bootstrap 配置、yuuagents `EventBus`、LLM `usage/cost`、tool `UsageSink` 和 yuubot `LLMBackendRecord.pricing` / actor `budget` 字段，但这些还没有形成完整的 trace/cost 链路。

这不是单纯缺少 Admin UI。风险点在于：系统运行时可能能执行 LLM/tool，但无法可靠记录 trace、聚合 usage/cost，也无法在 pricing 缺失时阻止产生不可预期账单。

## 当前状态

- `TraceService.start()` / `stop()` 为空实现，没有启动或初始化 yuutrace。
- daemon 没有调用 `yuutrace.init(...)` / `yuutrace.disable()`。
- yuuagents `Stage.eventbus` 没有订阅 trace observer。
- LLM 调用结束后虽然 yuuagents 能拿到 `store.usage` / `store.cost` 并 charge budget，但 yuubot 没有把它写入 yuutrace event。
- Integration capability 可以通过 `InvocationContext.charge_usage()` 上报 tool/integration usage，但没有统一转成 yuutrace cost/usage event。
- `LLMBackendRecord.pricing` 已持久化，但当前没有用于 cost 计算或强制校验。
- Admin `/monitor/` proxy 和 cost panel 尚未实现，但它们依赖后端 trace/cost 链路先打通。

## 目标

把 trace/cost 作为后端一等能力打通，确保在 UI 之前已经满足：

1. 每次 actor turn 有可查询 trace。
2. 每次 LLM request 记录 token usage。
3. 每次可计费 LLM request 记录 cost。
4. 每次 tool/integration 上报的 usage 能进入统一观测链路。
5. Actor / backend 配置了 budget 时，缺失 pricing 的可计费模型应拒绝执行或明确要求禁用预算保护。

## 设计要点

### 1. TraceService 真实启动

- 根据 `trace.enabled` 初始化或禁用 yuutrace。
- collector/UI 优先复用 yuutrace 已有 server/UI 能力。
- collector port 和 UI port 严格按 bootstrap config 区分。
- daemon status 需要暴露 trace service 的真实状态，而不只是 `enabled` 配置值。

### 2. yuuagents EventBus observer

- Actor runtime 创建 `Stage` 后，订阅一个 yuubot-owned observer。
- observer 处理至少这些事件：
  - `llm.started`
  - `llm.finished`
  - `runtime.usage_reported`
  - `runtime.task_created`
  - `runtime.task_completed`
  - `runtime.task_error`
  - `budget.exceeded`
- observer 只做事件转换，不把 yuutrace 细节泄露进 actor / integration 核心逻辑。

### 3. LLM usage / cost 记录

- 从 `llm.finished` payload 读取 `usage`、`cost`、`model`、`agent_id`、`agent_name`。
- 使用 yuutrace 的标准 API 记录：
  - `record_llm_usage(...)`
  - `record_llm_cost(...)` 或 `record_llm_usage(..., cost=...)`
- 如果 provider 返回了 `store.cost`，优先使用 provider cost。
- 如果 provider 只返回 usage，则根据 `LLMBackendRecord.pricing` 计算 cost。

### 4. Pricing / budget 安全校验

- Actor 配置了 `budget.max_usd` 或 backend 配置了 `BudgetPolicy` 时，模型必须能找到 pricing。
- 找不到 pricing 时，LLM 调用前拒绝执行，错误需要明确提示缺失的 backend/model。
- 避免静默按 0 成本运行。

### 5. Tool / Integration usage 记录

- `InvocationContext.charge_usage()` 继续作为 integration 上报入口。
- `runtime.usage_reported` 统一转为 yuutrace tool usage / cost event。
- metadata 中保留 `actor_id`、`integration_id`、`capability_id`、`task_id`。

### 6. Admin Monitor 是后续展示层

- 后端 trace/cost 链路完成后，再实现 Admin `/monitor/` proxy。
- Cost panel 只读后端已经落地的 trace/cost 数据，不在前端补算关键账单逻辑。

## 验收标准

- daemon 启动时 trace enabled 会初始化 yuutrace，disabled 时不会写 trace。
- 一次 echo HTTP E2E / actor turn 能产生可查询 conversation/turn trace。
- scripted LLM 返回 usage 后，trace 中能看到 token usage。
- 有 pricing 时能记录 USD cost；无 pricing 且 budget 开启时拒绝执行。
- integration capability 调用 `charge_usage()` 后，trace 中能看到 tool/integration usage。
- `uv run ty check` 和 `uv run pytest` 通过。

## 影响范围

- `process.py` — `TraceService` 从空实现变成真实 yuutrace lifecycle。
- `core/assembly.py` / actor runtime 创建处 — 注入 eventbus observer。
- `core/actors/simple_loop.py` — actor turn trace 上下文边界。
- `core/integrations/context.py` — usage metadata 约定可能需要补充。
- `resources/records.py` — pricing / budget 字段语义需要保持稳定。
- `runtime/daemon.py` — status 暴露真实 trace 状态。
- Admin UI — 后续增加 `/monitor/` proxy 和 cost panel。
