# RFC: Use yuullm Session to Hide Provider State

## Scene

A user asks an agent to inspect a repository, run a tool, and summarize the
result. The agent should be concerned with the conversation history, the model
intent, and the runtime work. The fact that the selected model is served through
OpenRouter first and Anthropic second should be a hidden implementation detail.

The first provider fails halfway through streaming a tool call, then a fallback
provider succeeds.

1. `Stage.from_config()` creates one `yuullm.ProviderPool` from the configured
   providers and model selector.
2. `create_agent()` builds the initial prompt prefix:

   ```python
   [
       yuullm.tools([...]),
       yuullm.system("You are a repo assistant."),
   ]
   ```

   It then creates `session = provider_pool.create_session(selector, history)`.
3. The user message arrives. `Agent.append_message(yuullm.user("Find the test failure"))`
   calls `session.append(...)` immediately.
4. `Agent.call_llm()` calls `stream, store = await session.stream(**stream_options)`.
   yuuagents observes the stream for UI, entity log, budget, and tool scheduling.
   It does not build or append the assistant message.
5. The first provider streams partial text and then fails. `YuuSession` yields:

   ```python
   yuullm.AttemptRecovery(
       failed_provider="openrouter",
       failed_model="model-a",
       next_provider="anthropic",
       next_model="model-b",
       rollback_to=yuullm.StreamCursor(history_len=3, stream_seq=0),
       continuation="non_seamless",
       reason="timeout",
   )
   ```

   yuuagents emits an event and lets the frontend reconcile visible output using
   the cursor. It does not patch the transcript.
6. The fallback provider emits:

   ```python
   yuullm.Response({"type": "text", "text": "I will run the tests."})
   yuullm.ToolCall(id="tc_1", name="bash", arguments='{"cmd":"pytest -q"}')
   ```

   At stream exhaustion, `YuuSession` appends the assistant message to its own
   history.
7. `Agent.call_tools()` runs the tool through `Runtime`. When the task resolves,
   `AgentLLMBridge` calls:

   ```python
   session.append(yuullm.tool("tc_1", "1 failed, 18 passed"))
   ```

   `YuuSession` validates that `tc_1` is an open tool call and closes it.
8. If the tool is detached and later completes, yuuagents injects a user message
   through the same boundary:

   ```python
   session.append(yuullm.user("Background task task_123 completed:\n..."))
   ```

After the turn, the agent's logical history is available through the session:

```python
[
    yuullm.tools([...]),
    yuullm.system("You are a repo assistant."),
    yuullm.user("Find the test failure"),
    yuullm.assistant(
        {"type": "text", "text": "I will run the tests."},
        {"type": "tool_call", "id": "tc_1", "name": "bash", "arguments": "..."},
    ),
    yuullm.tool("tc_1", "1 failed, 18 passed"),
]
```

yuuagents can copy or inspect that history when it needs to fork, roll over, or
persist agent state. The important point is that those operations read from the
session's canonical provider-facing history instead of maintaining a second raw
list.

yuuagents records runtime events and entity output beside that history:

```python
RuntimeEvent(
    name="llm.recovered",
    data={
        "agent_id": "agent_abc",
        "failed_provider": "openrouter",
        "next_provider": "anthropic",
        "rollback_to": {"history_len": 3, "stream_seq": 0},
        "continuation": "non_seamless",
    },
)
```

## Guiding Opinion

yuuagents should put its weight on managing history and model intent. Provider
selection, fallback, retry state, stream rollback, provider-specific message
conversion, usage, and cost should be details that yuullm can hide.

The stateless API makes that impossible. Once yuuagents calls
`client.stream(history, model=...)`, yuullm only sees one isolated request. It
cannot reliably connect calls into a session, know which assistant/tool messages
were committed after a previous stream, or treat provider failure recovery as a
stateful operation. yuuagents then has to duplicate LLM protocol work to keep
the next call legal.

The session API keeps the boundary in the right place. yuuagents still manages
history as an agent concern: it creates the initial history, appends user and
background messages, reads history for rollover, and chooses the model selector
for the agent. But it performs those operations through `YuuSession`, so yuullm
can own the provider-facing call state and hide provider details.

The design target is:

- yuuagents thinks in terms of `history`, `model selector`, `tool calls`, and
  agent lifecycle.
- yuullm thinks in terms of providers, resolved model bindings, streaming
  retries, message normalization, assistant commits, and accounting.

## Walk

The same scenario under the proposed design has one canonical history mutation
path.

`Agent.append_message()` no longer queues pending user messages. It delegates to
`YuuSession.append()` and resets the agent's done flag. yuuagents still decides
that the message belongs in the agent history. yuullm validates that adding it
keeps the provider-neutral chat protocol legal.

`Agent.call_llm()` no longer passes `history` into a stateless client. It starts
the session stream, observes items, and derives ephemeral turn state:

- response text for `llm.finished`
- streamed reasoning and content blocks for `EntityLog`
- tool calls to schedule after the stream completes
- recovery events for observability
- usage and cost for `Budget`

When the iterator is exhausted, `YuuSession` has already committed the
provider-normalized assistant message. `Agent.call_llm()` reads the last
committed assistant message from the session for return value and event
payloads. It does not reconstruct the message from stream chunks.

`AgentLLMBridge` no longer writes to `agent.history`. It receives the completed
tool outputs from runtime tasks, emits entity log blocks, and appends tool
messages through `session.append()`. The bridge still owns task linkage,
foreground wait, detach, interrupt, and terminal background notifications.

`replace_history()` becomes a session replacement operation, not mutation of a
detached raw list. The agent receives a new `YuuSession` created from the same
session factory and the replacement history. Pending tool links are cleared
because the replacement transcript is a new protocol state.

## Decision

Completely switch yuuagents from stateless LLM clients to `yuullm.YuuSession`.

There should be no long-lived compatibility layer where yuuagents keeps both
`LlmClient.stream(history, ...)` and `YuuSession.stream(...)` as first-class
paths. The package boundary should be:

- yuuagents owns the agent's logical history, model selector, agent lifecycle,
  budget policy, runtime task scheduling, background task behavior, entity
  logging, event emission, mailbox notifications, and tool backend execution.
- yuullm owns provider APIs, provider selection, resolved model bindings,
  retry/fallback state, stream rollback, provider-facing history invariants,
  assistant message construction, tool result validation, usage, and cost.

This keeps each package focused on its own responsibility. LLM protocol details
should not be duplicated in yuuagents.

## Interfaces

### Session Factory

yuuagents should depend on a narrow session factory protocol instead of a
stateless client protocol:

```python
class LlmSessionFactory(Protocol):
    def create_session(self, history: yuullm.History) -> yuullm.YuuSession:
        ...
```

Production factories wrap `yuullm.ProviderPool` plus a selector:

```python
@define
class ProviderPoolSessionFactory:
    pool: yuullm.ProviderPool
    selector: str

    def create_session(self, history: yuullm.History) -> yuullm.YuuSession:
        return self.pool.create_session(self.selector, history=history)
```

Tests should use fake `YuuSession` objects or a fake factory that implements the
same session surface. They should not keep a fake stateless `LlmClient`.

### Agent

`Agent` should hold a session and treat it as the canonical history surface:

```python
@define
class Agent:
    agent_id: str
    llm_session: yuullm.YuuSession
    llm_session_factory: LlmSessionFactory
    budget: Budget
    runtime: Runtime
    eventbus: EventBus
    llm_options: LlmOptions = field(factory=dict)
```

`Agent.history` should remain available as an agent-level concept, but it should
be a property over `llm_session.history`, not a second mutable list. Copying that
history for rollover, persistence, or child-agent creation is valid. Appending
to it outside `YuuSession.append()` is not.

### Stage

`Stage` should store session factories, not LLM clients:

```python
@define
class Stage:
    llm_session_factories: Registry[LlmSessionFactory]
    llm_options: Registry[LlmOptions]
```

The existing `register_llm_provider()` registry should become a registry of
session factory builders. Built-in provider config should create a
`ProviderPoolSessionFactory`.

### Configuration Boundary

Config remains a yuuagents serde boundary because `Stage.from_config()` owns
application assembly. The boundary should be explicit `msgspec.Struct` models.

One provider-pool shape is:

```python
class LlmProviderSpecConfig(msgspec.Struct):
    name: str
    api_type: str
    api_key_env: str = ""
    base_url: str = ""
    extra: LlmOptions = msgspec.field(default_factory=dict)


class LlmSessionConfig(msgspec.Struct):
    selector: str
    providers: list[LlmProviderSpecConfig]
    stream_options: LlmOptions = msgspec.field(default_factory=dict)
    judge_provider: str | None = None
    judge_model: str | None = None
```

The config is converted once at `Stage.from_config()`. Inside yuuagents, code
passes typed config and `yuullm.ProviderSpec` objects, not raw dictionaries.

## Invariants

1. yuuagents manages history as an agent concern, but canonical mutation goes
   through `YuuSession`.
2. yuuagents never appends directly to a raw transcript list.
3. yuuagents never reconstructs assistant messages from stream items.
4. Every user, tool, and background notification message enters through
   `YuuSession.append()`.
5. `Agent.call_llm()` only observes stream items and records side effects owned
   by yuuagents.
6. `Agent.call_tools()` only schedules runtime work and appends tool results
   through the session.
7. `AttemptRecovery` is an observability event, not a transcript mutation.
8. Replacing history creates a new session. It does not mutate the old session
   while tasks are in flight.

## Migration Plan

1. Add the yuuagents session factory protocol and replace `LlmClientBundle` with
   a session-factory bundle.
2. Change `Stage` to create and store session factories.
3. Change `create_agent()` to build the initial history and create a
   `YuuSession`.
4. Change `Agent` fields from `history` plus `llm` to `llm_session` plus
   `llm_session_factory`.
5. Change `Agent.append_message()` and background completion injection to call
   `llm_session.append()`.
6. Change `Agent.call_llm()` to call `llm_session.stream()`, observe stream
   items, and read the committed assistant message from session history.
7. Change `AgentLLMBridge._record_tool_results()` to call
   `llm_session.append(yuullm.tool(...))`.
8. Remove the old `LlmClient` protocol, fake stateless LLM tests, and direct
   `agent.history.append(...)` sites.
9. Add regression tests for:
   - user message append delegates to session validation
   - assistant message is committed by session, not yuuagents
   - tool result append rejects unknown tool call ids
   - provider recovery emits `llm.recovered`
   - detached background completion enters as a user message through session

## Non-Goals

- Do not keep a stateless-client adapter as a supported runtime path.
- Do not let yuuagents choose provider fallback order. That belongs to
  `yuullm.ProviderPool`.
- Do not duplicate yuullm's assistant-message construction in yuuagents.
- Do not add frozen attrs or msgspec models by default. Use mutable models unless
  a local invariant requires immutability.

## Open Questions

1. Should `llm.started` report the resolved provider/model before streaming, or
   should that remain unknown until `store.usage` and pool call records are
   available?
2. Should `AttemptRecovery.rollback_to` be passed through as a raw
   `StreamCursor`, or converted into an event DTO for consumers that serialize
   runtime events?
3. Should `Agent.replace_history()` be kept as a public API, or should callers
   create a new agent when they want a new transcript?
