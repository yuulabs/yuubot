# V2 Roadmap: Observability Regions and Terminal-Like Tool Output

The runtime needs a clearer model for observable execution regions and terminal-like output. This is v2 roadmap work, separate from the immediate backend correctness fix for persisted tool results.

## Problem

Current output handling is too close to an append-only event log for frontend display. That is insufficient for terminal-like streams such as Python `tqdm`, progress bars, carriage returns (`\r`), backspaces (`\b`), and ANSI control sequences.

At the same time, preserving every raw stdout/stderr/control chunk as trace or conversation history would explode storage and context size.

## Direction

- Define explicit observability regions for actor turns, LLM output, tool execution, and terminal/display output.
- Separate runtime observability from frontend display and persisted conversation history.
- Model tool execution output as a terminal-like display region with a mutable view, not only as append-only text.
- Keep raw terminal/control input short-lived or opt-in for debug capture.
- Store semantic trace events and compact output summaries by default, not every high-frequency rendering chunk.
- Let frontend SSE expose a simple display protocol for current output state, rather than leaking raw runtime event names.

## Non-Goals

- Do not make trace storage a terminal recording system by default.
- Do not persist every intermediate progress-bar update into conversation history.
- Do not require the Admin UI transcript reducer to understand low-level runtime event names.

## Scenario

```text
Python tool writes:
  "10%|#         | 1/10\r"
  "20%|##        | 2/10\r"
  ...

Runtime terminal buffer applies carriage returns:
  current stdout view = "20%|##        | 2/10"

Frontend receives throttled terminal view updates:
  tool_output_snapshot(call_id, stdout lines=["20%|##        | 2/10"])

Trace records:
  tool started
  output stats/summary
  tool finished
  final result metadata
```

## Relationship to Immediate Work

The immediate Phase 4 instruction must fix backend correctness for final `tool_result` text and SSE protocol clarity. The larger runtime terminal buffer, observability-region model, throttled display snapshots, and opt-in raw terminal capture belong to this v2 roadmap.
