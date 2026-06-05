---
name: scenario-first
description: Drive technical explanations with concrete scenarios before abstractions. Use this when explaining system designs, RFCs, architecture decisions, or any technical concept where the reader needs to understand data flow and causality. Activates when design documents, architecture explanations, RFC reviews, "how does X work" questions, and any technical writeup that risks becoming abstract without grounding.
---

# Scenario-First Technical Explanations

## Core Principle

**Always open with a concrete scenario.** A scenario is a specific, sequential trace of what happens — who calls whom, what data flows where, what goes wrong. It is not a list of problems. It is a narrative with actors and events.

A technical explanation that starts with "Problem 1, Problem 2, Problem 3" is already lost. The reader cannot evaluate whether your solutions are correct because they don't know what world your solutions live in.

## Pattern: Scene → Walk → Dissect

1. **Scene** — Write one concrete scenario that exercises the key design tension. Show the full call flow with timestamps, data shapes, and failure modes. The scenario should be specific enough that a reader can trace "and then what happens?" from start to end.

2. **Walk** — Walk through the scenario again, this time showing how your proposed design handles each step. Show actual data — what the DB rows look like, what the JSON payload contains, what the span attributes are. The reader should be able to verify your claims against the scenario.

3. **Dissect** — Now that the reader has seen the design in action, explain the abstractions, constraints, and tradeoffs. This is where you name your classes, define your types, and list your invariants.

Never reverse this order. Abstractions before scenarios are assertions without evidence.

## Anti-Patterns

### Listing problems without a scenario

```
❌ Bad:
## Problems
1. LLM streaming output is invisible
2. All running entities handle I/O differently
3. TurnContext batch model loses data on crash
```

These are true but contextless. The reader cannot tell which problems matter, how they interact, or whether your solution actually addresses them.

### Introducing abstractions before showing what they do

```
❌ Bad:
## Design
Each entity holds an ObservableBuffer. The PeriodicBridge subscribes to
both stdout and stderr, flushing to yuutrace and emitting to EventBus...
```

The reader has no idea why ObservableBuffer exists, what PeriodicBridge bridges, or what happens when the concrete scenario plays out.

### Using the design to validate itself

```
❌ Bad:
This design solves the problem because EntityLog.write() notifies subscribers,
and PeriodicReporter reads increments...
```

You cannot use the design to prove the design works. You prove it by running the scenario through the design and showing the data.

## Writing Scenarios

A good scenario:

- Has named actors (agent, bash process, frontend)
- Shows data at rest (DB rows, JSON payloads, span attributes)
- Includes a failure or edge case (crash, timeout, parallel execution)
- Is specific enough to falsify — if your design doesn't handle a step, the reader can point to exactly where it breaks

Example pattern:

```
1. User sends message → agent receives it
2. Agent calls LLM → LLM thinks for 30s (frontend sees nothing)
3. LLM returns tool_call(bash, command="find / -name '*.log'")
4. Agent calls bash → bash runs for 60s (stdout trickles out)
5. Bash finishes → result flows back to agent
6. Agent calls LLM again → LLM gives final answer
```

Then show what the data looks like in your storage layer:

```
entity       (agent_abc, type=agent, parent_id=)
entity.chunk (agent_abc, index=0, blocks=[
  ContentBlock(block_id=0, content="Let me think..."),
  ContentBlock(block_id=2, content={"type": "tool_call", "id": "tc_1", ...}),
])
entity       (bash_456, type=bash, parent_id=agent_abc)
entity.chunk (bash_456, index=0, blocks=[
  ProcessBlock(block_id=0, content="Finding...\n", stream="stdout"),
])
entity.chunk (agent_abc, index=1, blocks=[
  ContentBlock(block_id=3, content={"type": "tool_result", "tool_call_id": "tc_1", ...}),
])
```

The reader can now verify: does `tc_1` link back correctly? Does `parent_id` establish the hierarchy? What if the process crashes after step 3 — how much data survives?

## Checklist

Before publishing a technical explanation:

- [ ] Does it open with a concrete scenario showing actors and data flow?
- [ ] Does the scenario include a non-trivial edge case (long operation, crash, parallel calls)?
- [ ] Does it show what data looks like at rest (DB, JSON, span attributes)?
- [ ] Are abstractions introduced only after the reader has seen them in action?
- [ ] Can a reader point to a specific scenario step and ask "what happens here?" and find the answer?
- [ ] Does it avoid listing problems without grounding them in a scenario?

## When Not To Use This

- One-line answers to fact questions
- Code review comments on specific lines
- Pure API documentation (parameters, return types)
- Incremental changes where the scenario is already established in prior context