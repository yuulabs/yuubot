# yuubot Backlog

> 未排进当前 sprint 的条目。每条带 constitution lineage(推进 M 1–5 中的哪一项)。
> 入 sprint 时走 NEW-REQ route 转 REQ-NNNN;不入 sprint 的不属于当前 sprint 范围。

## Items

- **Observability regions & terminal-like tool output** — 定义 actor turn / LLM / tool / display
  的可观测区域;tool 输出以可变终端视图建模(`\r`/`\b`/ANSI),而非只追加文本;
  trace 默认存语义事件 + 紧凑摘要,不存每个渲染块。详细方向见
  `roadmap/archive/v2-observability-terminal-output.md`。推进 **Constitution M1
  (observability) + M5(可观测性/可解释性)**。lazy: 原散件已归档,此处仅一行索引。
- **`monitor` LLM 计时显示 bug**(来自 `warroom/TODO.md`)— cost-guard 合并后,
  monitor 的 Tool Execution 占比 ~100%,因 LLM 执行时间报为 0。非阻塞,不影响
  budget / cost 事件 / dashboard。疑似 LLM 计时 emit 问题。推进 **M1
  (observability)**。
