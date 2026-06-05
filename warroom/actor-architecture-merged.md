# Actor Architecture: Unified Design

Created: 2026-06-04

---

## 1. Core Principle

**Message is input, not a function call. Actor processes input and produces events.
There is no return value.**

Agent output is three things:
1. **Facade calls** — agent explicitly calls `yb.im.respond()`, `yext.*.call()`, etc.
2. **EventBus events** — thinking, text, tool_call, tool_result (already in yuuagents)
3. **State changes** — agent history growth, budget consumption

---

## 2. Two Output Modes, Same Agent

yuubot actors serve two fundamentally different interaction modes that have been
conflated in the current `SimpleLoopActor`:

### IM Mode

IM mode is for tasks where the user doesn't need to see the details — "run this
experiment overnight", "watch this channel for me". The user sends a message and
gets a result back. Internally, the actor may do complex work: massive rollovers,
spawning sub-agents, long-running tool chains. The user only sees what the agent
chooses to send via `yb.im.respond()`.

IM mode uses the public mailbox channel, same as any integration. The actor
decides how to process incoming messages — it's not fundamentally different from
how Telegram or Discord integrations work. Messages arrive, actor handles them,
responds when it wants.

- Agent explicitly calls `yb.im.respond(msg_id, text="...")` to reply
- This goes through bridge → daemon → `integration.response()`
- If the agent doesn't call `yb.im.respond()`, the user gets no reply
- System prompt must instruct the agent to use `yb.im.respond()` for IM messages
- Agent may call `yb.im.react(emoji="working")` for progress indication

**IM mode does NOT:**
- Stream thinking blocks to the user
- Show tool call process
- Use `turn_results` queue (deleted)
- Block waiting for synchronous response

**IM mode DOES:**
- Use `yb.im.respond()` / `yb.im.react()` for all user-facing output
- Send error text on failure (via `yb.im.respond()` or fallback)
- Maintain persistent agent context across messages
- Internally run complex workflows (rollover, parallel agents, etc.) — user only sees the output

### Conversation Mode

A conversation is a dedicated session with a single agent. The user has full
visibility into the agent's internal execution — thinking, tool calls, streaming
output. This demands a **dedicated channel**: conversation messages cannot be
interrupted by messages from other channels (IM, other integrations), and the
system must provide observability support for the agent's process.

Compared to IM mode, conversations offer **fine-grained internal control**: you
can see what the agent is doing, intervene, and steer. IM mode is fire-and-forget
— you send a message and get a response with no visibility into the process.

Showing sub-agents in conversation mode is an observability and frontend challenge
that can be addressed later. For now, a conversation shows one agent's execution.

- Frontend subscribes to yuuagents EventBus events
- Events are streamed to frontend via WebSocket/SSE and persisted to conversation store
- No facade call needed for output — the agent just processes, and events flow naturally
- The llm block message event system is designed for this streaming path
- Conversation has its own dedicated channel — no cross-channel message interference

**Conversation mode does NOT:**
- Use `integration.response()` — it's not an integration
- Flatten thinking into text
- Block synchronously with a 5s timeout
- Receive messages from other channels (IM, other integrations) mid-session

**Conversation mode DOES:**
- Stream structured ContentItems to the frontend
- Preserve thinking blocks, tool calls, and text as separate items
- Persist full dialog history per conversation
- Allow agent expiration and re-creation from history
- Have a dedicated channel isolated from the mailbox

### Web Chat Has Two Entry Points

| Entry Point | Mode | Channel | What You Talk To | Output |
|---|---|---|---|---|
| **Actors** | IM | Public mailbox (shared with integrations) | The actor directly | `yb.im.respond()` → `integration.response()` |
| **Conversations** | Conversation | Dedicated (isolated, no cross-channel interference) | An actor's agent | Structured ContentItem stream via EventBus |

---

## 3. Key Concepts

### Actor = Resource Bundle

An actor owns:
- LLM backend + model
- Character (system prompt, persona)
- Tool configs + allowed capabilities
- Python session (yb + yext facade)
- Agent instance(s) — same resources, different execution contexts
- Mailbox (for IM messages)

An actor does NOT own conversation history. Conversations are separate entities
that reference an actor.

### Agent = Execution Instance Sharing Actor's Resources

An actor's agents are different execution instances that share the same resource
bundle — same character, same LLM, same tools, same capabilities. They are NOT
different personas. If you need a different character or LLM config, create a
different actor.

Agents within an actor are like different "workers" of the same persona:
same identity, same skill set, but independent execution contexts. Currently
each actor has one agent ("main"). In the future, an actor might spawn multiple
agents for parallel tasks, background work, etc.

The framework supports multiple agents per actor from day one, but every agent
inherits the actor's full resource bundle — no per-agent character or LLM override.

### Conversation = Temporary Session with History

A conversation:
- References an actor (all agents share the same resources)
- Owns the dialog history (messages, thinking, tool calls)
- Is NOT permanently bound to a live agent instance
- Persists across agent expirations
- Offers fine-grained control: visibility into thinking, tool calls, ability to intervene

When a new agent is created for a conversation, it initializes its context
from the conversation's history — just like a normal chatbot.

### Agent Instance = Ephemeral LLM Context

An agent instance:
- Is created from the actor's resource bundle (same character, LLM, tools for all agents)
- Initializes from a conversation's history (if any)
- Has its own context window, tool execution state
- Shares the actor's python session (never refreshed across rollovers — variables persist)
- May expire (idle timeout, budget exhaustion)
- Is replaceable — a new agent picks up from conversation history

### StandardActor Behavior

The standard actor (which we'll call **StandardActor**) has a clear behavior
for handling mailbox messages:

1. **Main agent**: When a message arrives in the main mailbox, the actor feeds
   it to its main agent. The main agent processes it, may call tools, may call
   `yb.im.respond()` to reply.

2. **Auto-rollover**: When the main agent's context approaches capacity
   (85% of max tokens), the actor triggers a rollover:
   - Copy the current agent (but NOT its python session — that stays shared)
   - Append a message to the copy asking it to summarize the context
   - The summarized context becomes the initial history for a fresh agent
   - The python session is NOT refreshed — variables persist across rollovers

3. **Internal complexity is hidden**: In IM mode, the user doesn't see rollovers,
   sub-agents, or internal state. They only see what `yb.im.respond()` sends back.
   An actor could run experiments overnight with dozens of rollovers and the user
   only sees the final result.

```
┌─────────────────────────────────────────────────────────────┐
│                    StandardActor                             │
│                                                              │
│  Mailbox ──▶ Main Agent ──▶ (85% tokens?) ──▶ Rollover     │
│                  │                            │              │
│                  │                   ┌────────┴────────┐    │
│                  │                   │ Copy agent       │    │
│                  │                   │ (no python sess) │    │
│                  │                   │ Ask: summarize   │    │
│                  │                   │ Summary → new    │    │
│                  │                   │ agent history    │    │
│                  │                   └─────────────────┘    │
│                  │                                          │
│                  ▼                                          │
│           Python Session (shared, never refreshed)          │
│           yb + yext facade                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Current State Summary

### Registered Actor Types (Production)

| Type | Factory | Status | Purpose |
|------|---------|--------|---------|
| `simple_loop` | `SimpleLoopActorFactory` | **Only production type** | Default yuuagents-backed agent loop |

### Test-Only Actor Types

| Type | Factory | Status | Purpose |
|------|---------|--------|---------|
| `echo` | `EchoOnceActorFactory` | Test-only, NOT registered in `default_actor_factories()` | Integration facade plumbing test |

### Key Finding: No `python_session` Actor Type

The codebase has `ExecutePythonSession` (in `python_session.py`), but it is **not an actor type** — it's a helper class owned by actors. `SimpleLoopActor` creates one via `ActorPythonSessionFactory.bind_facade()`. There is no standalone "python session" actor type.

### SimpleLoopActor: Current Behavior (What Exists)

```python
# What simple_loop actually does:
while True:  # message loop
    message = await mailbox.recv()
    agent.append_message(render(message))
    await run_agent_loop(agent)  # single turn
    # agent history grows unboundedly
    # no mid-turn message drain
    # no compaction
    # no idle timeout
```

Despite the name, the actual agent execution is a **single turn** per message:
1. `_consume_messages()` IS a loop — it loops over incoming messages from the mailbox
2. But each message triggers exactly ONE `run_agent_loop()` call
3. There is NO continuous agent loop that drains new messages mid-turn
4. There is NO auto-compaction/rollover
5. There is NO idle timeout / auto-shutdown

The `ScheduleTriggerMessage` wrapping in `handle_message()` is a hack — it wraps
the rendered user message as a `ScheduleTriggerMessage(agent_name=actor.name, content=yuullm.Message)`,
which is an ugly workaround for passing messages into the agent runtime.

### yb Facade: Current State

| Module | Status | Functions |
|--------|--------|-----------|
| `yb` | ✅ Working | `import yb` → `yb.actor`, `yb.tasks` |
| `yb.actor` | ✅ Working | `current()`, `context()`, `actor_id()`, `agent_name()`, `session_id()`, `mailbox_id()` |
| `yb.tasks` | ✅ Working | `submit_bg(coro)` — submit background task, notify bridge |
| `yb.admin` | ✅ Working | `chat.get_dialog()` is exported and wired through system bridge to `ChatStore` |
| `yb.im` | ✅ Working | `respond()`, `react()` route through bridge to `integration.response()` |
| `yb.schedule` | ✅ Working | Cron helpers call the actor's yuuagents `schedule` executor |
| `yb.delegate` | ✅ Working | `submit()` starts same-actor delegate agents and reports completion to the parent |

Note: `yb.webui` was originally planned as a separate module, but its functionality
is semantically absorbed by `yb.admin`. The design should reflect `yb.admin` as the
web UI interaction module rather than having a separate `yb.webui`.

### Remaining Product Gaps

1. **Message Rendering Policy** — `render_incoming_user_message()` still uses a single metadata format for integration messages. A richer policy may distinguish first message vs. continuation, system vs. integration, and background completion context.
2. **Memory Policy** — `memory_enabled` and `memory_curator_enabled` are declared but not yet wired to a memory subsystem.
3. **Resource Policy** — `concurrency_limit` and some workspace policy knobs still need enforcement paths.
4. **Character Hints** — `facade_module` and `default_hints` are stored but not yet projected into prompt/facade behavior.
5. **Frontend Actor Configuration** — Actor type, tool selection, and capability filtering need stronger admin UI controls.
6. **Admin UI Interaction Facade** — Partial updates and buttons/forms need a concrete frontend event/data contract before implementation.

---

## 5. What to Delete

| Component | Reason |
|-----------|--------|
| `SimpleLoopTurnResult` | Messages have no return value |
| `turn_results` queue | Messages have no return value |
| `next_turn_result()` | Messages have no return value |
| `_last_assistant_text()` | No auto-extraction of response text |
| `_turn_result()` helper | No turn result to extract |
| Web Chat sync endpoint `POST /api/chat/{id}/messages` | Replaced by Conversation API + WebSocket |
| `_maybe_react_working()` on actor | Replaced by agent calling `yb.im.react()` |
| `_maybe_send_error()` on actor | Replaced by agent error handling or `yb.im.respond()` |

---

## 6. What to Create

### New Facade Module: `yb.im`

```python
# yb/im.py — IM response facade for agent use

async def respond(text: str, *, msg_id: str | None = None) -> dict:
    """Send a text response to the current conversation.
    
    If msg_id is provided, responds to that specific message.
    If msg_id is None, responds to the most recent incoming message.
    """

async def react(emoji: str, *, msg_id: str | None = None) -> dict:
    """Send a quick reaction (emoji) to indicate processing or acknowledgment."""
```

### New: Conversation Store

Independent of ChatStore. Stores structured content items.

```python
class Conversation:
    conversation_id: str
    actor_id: str
    created_at: datetime
    updated_at: datetime

class ConversationMessage:
    message_id: str
    conversation_id: str
    role: str  # "user", "assistant", "system", "tool"
    content: list[ContentItem]  # structured: thinking, text, tool_call, tool_result
    created_at: datetime
    metadata: dict  # token usage, model, cost, etc.
```

### New: Conversation Manager

Manages conversation lifecycle, agent creation, and event subscription.

```python
class ConversationManager:
    async def ensure_agent(self, conversation_id: str) -> Agent:
        """Get or create an agent for this conversation.
        If agent exists and is alive, bind it.
        If not, create from conversation history."""
    
    async def subscribe_events(self, conversation_id: str) -> AsyncIterator[AgentEvent]:
        """Subscribe to agent events for this conversation.
        Yields thinking, text, tool_call, tool_result events."""
```

### New: Agent Event Stream

Subscribe to yuuagents EventBus and forward relevant events to consumers.

```python
class AgentEvent:
    conversation_id: str
    agent_id: str
    event_type: str  # "thinking", "text", "tool_call", "tool_result", "error"
    content: ContentItem
    timestamp: float
```

### New: Public Agent API

```
POST /api/conversations/{id}/agents        — Create or bind an agent for a conversation
GET  /api/conversations/{id}/events        — WebSocket/SSE: stream agent events
GET  /api/conversations                    — List conversations for an actor
POST /api/conversations/{id}/messages      — Send a message (async, no response body)
```

### New: IM Output Handler

Subscribes to agent events and extracts facade calls.
When agent calls `yb.im.respond()`, the bridge routes it to `integration.response()`.

This is already partially implemented — `yb.tasks.submit_bg()` already uses the bridge
pattern. `yb.im.respond()` would follow the same pattern.

---

## 7. What to Refactor

### SimpleLoopActor → StandardActor (event-driven)

The actor no longer produces "turn results". It processes messages and lets
events flow through EventBus and facade calls.

The new StandardActor replaces SimpleLoopActor with:
- Event-driven output (no turn result extraction)
- Main agent for mailbox messages
- Auto-rollover at 85% max tokens (copy agent, ask to summarize, use summary as
  new agent's initial history, python session persists)
- Dedicated channel for conversations (no cross-channel interference)

```python
# Before (request-response):
async def handle_message(self, message):
    agent = await runtime.handle_message(...)
    result = _turn_result(agent)
    await self.turn_results.put(result)

# After (event-driven):
async def handle_message(self, message):
    # EventBus events flow automatically
    # Agent calls yb.im.respond() or other facades explicitly
    # Conversation mode subscribes to EventBus separately
    await runtime.handle_message(...)
```

### YuuAgentsActorRuntime → Agent lifecycle managed by Conversation

Currently the runtime owns agents permanently. In the new model:
- Conversations own agent history
- Agent instances are ephemeral (created from history, may expire)
- The runtime provides agent creation and event subscription, not permanent ownership

### Web Chat → Conversation API + WebSocket

Replace the synchronous `POST /api/chat/{id}/messages` with:
- `POST /api/conversations/{id}/agents` — ensure agent exists
- `POST /api/conversations/{id}/messages` — send message (async, no response body)
- `GET /api/conversations/{id}/events` — WebSocket/SSE for streaming events

### Message Rendering → Source-Aware

Current: `render_incoming_user_message()` always prepends `[sender time]`.

New: Rendering depends on mode:
- IM mode: prepend sender metadata (platform-specific formatting)
- Conversation mode: pass content items directly, no prefix mangling
- System messages: render as system instructions

---

## 8. What Stays the Same

- **Actor as resource bundle**: LLM, character, tools, capabilities, python session
- **yb / yext facade boundary**: Handwritten system facade vs generated integration facade
- **Mailbox for IM messages**: Integration messages still go through gateway → mailbox (public channel, actor decides how to handle)
- **Python session**: ipykernel subprocess for facade execution
- **Background tasks**: `yb.tasks.submit_bg()` works the same in both modes
- **Actor reconciliation**: start/stop/reconcile lifecycle unchanged
- **Facade code generation**: Same mechanism for both modes

---

## 9. Architecture: Two Modes, One Actor

```
┌─────────────────────────────────────────────────────────────────┐
│                         Actor (StandardActor)                    │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Agent Instance(s) — same character, LLM, tools, caps   │  │
│  │  (currently one: "main"; future: parallel tasks, etc.)   │  │
│  │  Auto-rollover at 85% tokens → summarize → fresh agent   │  │
│  └──────────────────────────┬───────────────────────────────┘  │
│                             │                                    │
│  ┌──────────────────────────┴───────────────────────────────┐  │
│  │  Python Session (yb + yext facade) — persists across     │  │
│  │  rollovers, never refreshed                                │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Mailbox (public channel)               │  │
│  │  Shared with integrations — actor decides how to handle  │  │
│  └──────────────────────────────────────────────────────────┘  │
└──────────┬──────────────────────────┬──────────────────────────┘
           │                          │
    ┌──────▼──────┐           ┌───────▼───────┐
    │  IM Mode    │           │ Conversation  │
    │             │           │    Mode       │
    │ Public      │           │ Dedicated     │
    │ mailbox     │           │ channel       │
    │ channel     │           │ (isolated)    │
    │             │           │               │
    │ "Run this  │           │ Fine-grained  │
    │  overnight" │           │ control:      │
    │             │           │ see thinking, │
    │ Output:     │           │ tool calls,  │
    │ yb.im       │           │ intervene    │
    │ .respond()  │           │               │
    │ → bridge    │           │ Output:       │
    │ → integ.    │           │ EventBus      │
    │ response()  │           │ → WebSocket   │
    └─────────────┘           └───────────────┘
```

### IM Mode: Detailed Flow

```
1. Telegram user sends "帮我看看 issue #42"
2. Integration ingress stamps source (producer="integration", id="telegram:amy")
3. Gateway routes to actor mailbox (public channel, same as any integration)
4. Actor decides how to handle — it's just another integration message
5. Actor feeds message to main agent
6. Actor renders message for IM context:
   "[Alice 14:30:05] 帮我看看 issue #42"
7. Agent processes (may involve rollovers, tool calls, long chains — user sees none of this)
8. Agent calls yb.im.respond(msg_id, text="Issue #42 是关于...")
   → bridge → daemon → integration.response()
9. Agent may call yb.im.react("working") for progress indication

Note: Internally, the actor may rollover multiple times, spawn sub-agents,
run long tool chains. The user only sees what yb.im.respond() sends back.
```

### Conversation Mode: Detailed Flow

```
1. Frontend loads conversation list
2. User selects conversation, sends message
3. Frontend calls: POST /api/conversations/{id}/agents
   → Backend checks: does this conversation have an active agent?
   → YES: bind existing agent, return agent info
   → NO: create new agent from conversation history, return agent info
4. Frontend establishes WebSocket/SSE connection (dedicated channel)
5. Message enters agent through conversation's dedicated channel
   — NOT through the public mailbox. No cross-channel interference.
6. Agent processes, EventBus events flow to frontend:
   - ThinkingBlock → rendered as collapsible section
   - TextItem → rendered as streaming text
   - ToolCallItem → rendered as tool execution card
   - ToolResultItem → rendered as result card
7. Agent may expire (idle timeout, budget exhaustion)
8. Next message → repeat from step 3 (create new agent from history)

Note: Sub-agents within a conversation are an observability/frontend challenge.
For now, a conversation shows one agent's execution. Sub-agent visibility is
a future enhancement.
```

---

## 10. yb Facade Modules: Target State

| Module | Status | Purpose |
|--------|--------|---------|
| `yb` | ✅ | Package root |
| `yb.actor` | ✅ | Actor context (actor_id, agent_name, session_id, mailbox_id) |
| `yb.im` | 🆕 | IM response: `respond()`, `react()` |
| `yb.tasks` | ✅ | Background tasks: `submit_bg()` |
| `yb.admin` | ✅ | Chat history access through system bridge; richer UI controls remain a separate product API decision |
| `yb.schedule` | ✅ | Scheduled triggers through yuuagents cron backend |
| `yb.delegate` | ✅ | Task delegation to same-actor agents |

---

## 11. Implementation Priority

### Phase 1: Foundation (unblocks both modes)

1. **Delete `turn_results` queue and `SimpleLoopTurnResult`**
   - Remove the request-response model from actor
2. **Create `yb.im` facade module**
   - `respond()`, `react()` — bridge calls to `integration.response()`
3. **Refactor `SimpleLoopActor.handle_message()`**
   - Remove turn result extraction
   - Agent output goes through EventBus + facade calls only
4. **Create Conversation Store**
   - Structured content items, not flat text
5. **Create Conversation Manager**
   - Agent lifecycle per conversation
   - History initialization

### Phase 2: Conversation Mode

6. **Subscribe to yuuagents EventBus for agent events**
   - thinking, text, tool_call, tool_result
7. **WebSocket/SSE endpoint for conversation events**
8. **Conversation API endpoints**
   - Create/bind agent, send message, list conversations
9. **Frontend: Workspace view with streaming**

### Phase 3: IM Mode Refinement

10. **System prompt guidance for `yb.im.respond()`**
    - Instruct agent to use `yb.im.respond()` for IM messages
11. **IM output handler**
    - Route `yb.im.respond()` bridge calls to `integration.response()`
12. **Remove `_maybe_react_working()` and `_maybe_send_error()`**
    - Agent handles these through `yb.im.react()` and `yb.im.respond()`

### Phase 4: Advanced

13. **Auto-rollover**
    - At 85% max tokens, copy agent (without python session), ask it to summarize
    - Summarized context becomes initial history for new agent
    - Python session persists across rollovers (variables never lost)
14. **Idle timeout**
    - Agent expires after idle period
15. **Multiple agent instances per actor**
    - Parallel tasks, background work, etc. — same resources, different execution contexts
16. **`yb.admin` facade**
    - Chat history access is wired through the system bridge.
    - Streaming output is handled by conversation EventBus/SSE, not by a facade call.
    - Partial updates and buttons/forms need a concrete frontend interaction contract before implementation.
17. **`yb.schedule` and `yb.delegate`**
    - Schedule uses the yuuagents cron backend and actor mailbox triggers.
    - Delegate starts same-actor worker agents with independent context and parent completion notification.

---

## 12. Open Questions

1. **How does `yb.im.respond()` know which message to respond to?**
   - Option A: Agent passes `msg_id` explicitly (from context)
   - Option B: Bridge tracks "current message" per agent session
   - Option C: `yb.im.respond()` responds to the most recent incoming message

2. **How does the agent know it's in IM mode vs Conversation mode?**
   - The system prompt should differ based on mode
   - `yb.actor.context()` could include a `mode` field
   - In IM mode, prompt instructs: "Use yb.im.respond() to reply"
   - In Conversation mode, prompt instructs: "Just process, output flows automatically"

3. **What happens when an agent in IM mode doesn't call `yb.im.respond()`?**
   - The user gets no reply (silent failure)
   - Should there be a fallback? A timeout? A warning?
   - Proposal: After agent turn completes without any `yb.im.respond()` call,
     emit a warning event. Optionally, auto-send a fallback message.

4. **How does Conversation mode handle multi-step agent loops?**
   - Agent calls tool → tool result comes back → agent continues
   - Each step produces EventBus events
   - All events stream to frontend in real-time
   - This is already how yuuagents works — we just need to subscribe

5. **What's the relationship between Conversation and Mailbox?**
    - IM messages go through Gateway → Mailbox (public channel, shared with integrations)
    - Conversation messages go through a dedicated channel (Conversation API → Agent directly)
    - Conversations are isolated — no cross-channel message interference
    - Background task completions still go through mailbox, and the actor
      decides how to route them (to IM agent or conversation agent)

6. **How should the multi-step loop interact with `run_agent_loop()`?**
   - Currently `run_agent_loop()` is a yuuagents function that runs the agent until it stops
   - Should we: (a) call it once per message then drain mailbox between calls?
   - (b) run a custom loop calling `agent.step()` individually, draining between steps?
   - (c) something else?

7. **What triggers the "done" condition?**
   - A tool call? A stop token? A specific message pattern?
   - Budget checks? Idle timeout?

8. **How does auto-rollover work with prompt caching?**
    - At 85% max tokens, copy agent (without python session), append summary request
    - The summarized context becomes the new agent's initial history
    - Python session persists across rollovers — variables are never lost
    - The checklist mentions "追加法" (append method) to leverage prompt caching
    - How should the summary be structured to maximize caching benefit?

9. **Should the actor manage its own Agent lifecycle, or should Conversation?**
   - Current: runtime creates agents lazily, keeps them forever
   - Proposed: conversations own history, agent instances are ephemeral
   - The runtime provides agent creation and event subscription, not permanent ownership

10. **How do conversations reference actors?**
    - Conversation stores: `actor_id`, `dialog_id`
    - When agent expires, conversation creates a new agent from history
    - If the actor's character or LLM config changes, the conversation
      picks up the new config on next agent creation (agents inherit
      current actor resources)

11. **What's the relationship between Conversation and ChatStore?**
    - Current `ChatStore` persists flat text messages
    - Conversations need structured content items
    - Should Conversation replace ChatStore, or extend it?

12. **Actor type selection: should it use class name?**
    - Current: `ActorRecord.type` defaults to `"simple_loop"`
    - Should use the class name for easier Reflection
