# Phase 4: Conversation Workflow

## Goal

Turn the current raw WebSocket/history page into a usable actor conversation experience.

Current issue: `web/src/features/conversations/conversation-detail-page.tsx` can send text over WS but lacks actor detail entry, draft/new flow, URL reconciliation, attachments, rich transcript rendering, tool/diff/thinking/cost display, and practical controls.

## User Flow

### Start from Actor

The primary entry should be:

1. User opens an Actor detail page.
2. User clicks "Start conversation".
3. Frontend navigates to `/admin/conversations/new?actor={actor_id}`.
4. Actor selector is locked because the draft is actor-bound.
5. First successful send creates or resolves a real conversation id.
6. URL switches to `/admin/conversations/{conversation_id}` without losing streamed output.

### Resume from history

Actor detail and conversation list should show conversation rows filtered by actor where appropriate. Opening an existing conversation should load history, costs, and live events for that id.

## Transcript Rendering

Replace raw JSON panels with a transcript model that handles:

- user/developer/assistant roles;
- streaming deltas;
- markdown;
- tool calls and tool results;
- file/image attachments when present in history/events;
- diffs and generated artifacts where event payloads expose them;
- thinking/reasoning blocks only if the backend emits a safe display shape;
- cost/usage badges from `GET /api/conversations/{id}/costs`.

Keep a collapsible raw event inspector for debugging, not as the primary view.

## Composer

The composer should support:

- multiline text;
- send on command/ctrl enter;
- interrupt current conversation;
- attachment upload to actor workspace before send;
- selected attachment chips;
- disabled state when actor or LLM is not configured.

Attachment upload should use Phase 1's corrected `file` form field. The message payload must reference uploaded workspace paths in the format the WebSocket command handler expects; confirm contract in `web/src/shared/lib/api/ws.ts` and `src/yuubot/web/ws.py` before implementing.

Current backend `conversation.send` accepts `content: ContentItem[]`, where file/image items can carry `path`, `url`, `mime`, and `meta`. Uploaded attachments should become `ContentItem` objects instead of ad hoc payload fields.

## WebSocket and ID Reconciliation

Define a small client-side conversation session state:

| State | Meaning |
| --- | --- |
| `draft` | URL has actor-bound draft id; no durable conversation id yet |
| `sending` | first message is in flight |
| `active` | durable conversation id known; history and costs can load by id |
| `interrupted` | user interrupted current run |
| `error` | send or WS failed |

The frontend should switch from draft id to real id after the backend sends `conversation.send.accepted` with `payload.conversation_id`. Do not infer the durable id from the draft URL.

Draft pages do not open a WebSocket. The first send pre-allocates a conversation id, navigates to `/admin/conversations/{conversation_id}` with the pending message in router state, then the durable page opens the WebSocket and sends.

### Stream transcript vs history re-fetch

During an active session, the main transcript is built from WebSocket stream events (`sessionTurns` + live blocks) plus `conversation.history.append` for durable user messages. Do **not** invalidate or re-fetch `GET /api/conversations/{id}/history` when `stream_stop` arrives.

Rationale: if the stream renderer is correct, its output must match persisted history exactly. Re-fetching history after every turn replaces the streamed view with the history renderer and hides stream-rendering bugs, which makes debugging harder. History is loaded once when opening a conversation (or on navigation); new turns in the current session stay on the stream path until the page reloads.

## Conversation History Rail

The conversation page should include a compact rail or switcher:

- filtered by active actor for actor-bound conversations;
- quick new conversation for the same actor;
- delete conversation action with confirmation;
- status/message count/last active timestamp.

## Tests

- Actor detail/list route starts an actor-bound draft.
- Draft route locks actor selection.
- First send transitions URL to durable conversation id when backend reports it.
- History rail filters by active actor.
- Composer upload uses `file` field and includes uploaded paths in send payload.
- Transcript renderer handles at least markdown, tool call, tool result, usage/cost, and raw fallback.
- Interrupt sends the correct WS command for the active conversation id.

## Backend Gaps

- Need stable display payloads for tool/diff/thinking/cost if not already emitted in history.
- Need frontend agreement on which uploaded workspace paths should be sent as `file` versus `image` `ContentItem`s.

## Acceptance Criteria

- A user can start a conversation from an actor without manually choosing ids.
- The URL reflects the real conversation after the first successful send.
- Conversation history is readable without opening raw JSON.
- Raw event inspection remains available for debugging.
