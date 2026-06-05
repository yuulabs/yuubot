# Bug: Assistant Response Content — Two Related Issues

## Issue 1: Trace UI Shows Empty Assistant Messages (FIXED)

**Root cause**: `YuuTraceObserver._on_llm_finished()` only called `turn.usage()` — never called `turn.add()` to write the LLM response content into the turn span. Result: `yuu.turn.items` was always `[]` in traces.db.

**Fix**: Added `turn.add(*message.content)` in `_on_llm_finished()` before `turn.usage()`, so the assistant's content items (text, thinking, tool calls) are now recorded in the turn span.

**File changed**: `yuuagents/src/yuuagents/observability.py` — `_on_llm_finished()` method.

---

## Issue 2: Web Chat Returns Raw Dict with Thinking Tags (DEFERRED)

### Symptom

Web chat response looks like:
```
[{"type":"text","text":"<thinking>The user is just saying hello...</thinking>Hello! How can I help you today? 😊"}]
```

### Root Cause Chain

1. **LLM returns thinking blocks**: DeepSeek (and other providers) return `ThinkingBlock` content items alongside text items.

2. **`agent.py` assembles the message** (`yuuagents/src/yuuagents/agent.py:127-135`):
   ```python
   assistant_items: list[Any] = []
   if text_chunks:
       assistant_items.append({"type": "text", "text": "".join(text_chunks)})
   assistant_items.extend(tool_call_items)

   message = yuullm.assistant(
       *[tb.to_message_item() for tb in thinking_blocks],  # thinking items
       *assistant_items,                                      # text + tool_call items
   )
   ```
   The `message.content` is a list of ContentItem dicts: `[{"type": "thinking", ...}, {"type": "text", "text": "Hello!"}]`.

3. **`_last_assistant_text()` flattens everything** (`yuubot-v2/src/yuubot/core/actors/impls/simple_loop.py:326-330`):
   ```python
   def _last_assistant_text(agent: Agent) -> str:
       for message in reversed(agent.history):
           if isinstance(message, yuullm.Message) and message.role == "assistant":
               return yuullm.render_message_text(message)
       return ""
   ```
   `render_message_text()` calls `render_item_text()` for each item, which renders thinking items as `<thinking>...</thinking>` tags. So the result is a **single string** like `"<thinking>...</thinking>Hello!"`.

4. **Daemon wraps it back into a ContentItem** (`yuubot-v2/src/yuubot/runtime/daemon/app.py:437-442`):
   ```python
   reply_content = [{"type": "text", "text": turn.assistant_text}]
   await chat_store.save_message(
       ...,
       raw_content=msgspec.json.encode(reply_content).decode(),
   )
   ```
   This creates a single TextItem whose `text` field contains the `<thinking>` tags mixed with the actual response.

5. **Web chat API returns** `{"reply": turn.assistant_text}` — the raw string with thinking tags.

### The Deep Problem

Thinking content and response content should **not** be concatenated into a single string. They are semantically different:

- **Thinking**: Internal reasoning, should be hidden from end users or shown in a collapsible UI element
- **Response text**: The actual answer the user sees

The current pipeline:
```
LLM → [ThinkingBlock, TextItem] → render_message_text() → "<thinking>...</thinking>Hello!" → single TextItem → raw_content
```

Should be:
```
LLM → [ThinkingBlock, TextItem] → preserve structured content → raw_content = [{"type": "thinking", ...}, {"type": "text", "text": "Hello!"}]
```

### Affected Code Paths

| File | Line | Current Behavior | Desired Behavior |
|------|------|-----------------|-----------------|
| `simple_loop.py` | 326-330 | `_last_assistant_text()` flattens to string with `<thinking>` tags | Should return structured content items, or at least filter out thinking |
| `daemon/app.py` | 437-442 | Wraps flat string into `[{"type": "text", "text": ...}]` | Should preserve structured content items |
| `daemon/app.py` | 455 | Returns `{"reply": turn.assistant_text}` | Should return structured content or separate thinking from text |

### Design Questions (Need Decision)

1. **Should `SimpleLoopTurnResult` carry structured content?** Currently it only has `assistant_text: str`. Adding `assistant_content: list[dict]` would let consumers access the structured items.

2. **Should the web chat API return structured content?** The `reply` field is currently a plain string. Changing it to a list of ContentItems would break backward compatibility unless versioned.

3. **Should thinking blocks be filtered from the user-facing response?** This is a product decision — some UIs show thinking in a collapsible section, others hide it entirely.

### Why This Is Deferred

This is a cross-cutting change that affects:
- `SimpleLoopTurnResult` data shape
- Web chat API response format
- Chat store persistence format
- Integration response format
- Trace UI rendering (now fixed for items, but thinking display is a UI concern)

It needs a design decision before implementation.