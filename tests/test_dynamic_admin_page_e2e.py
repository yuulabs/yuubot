from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast
from urllib.parse import quote

import httpx

from yuubot.domain import GenToolCall, LLMInput, StreamEvent, ToolResult

from support.api import (
    JsonObject,
    SharedTestContext,
    base_url,
    conversation_history,
    enable_actor,
    http_json,
    wait_for_history_kind,
    ws_conversation_send,
)
from support.llm_rules import (
    all_of,
    call_tool,
    has_tool_spec,
    messages_contain_tool_result,
    prompt_contains,
    reply_text,
    user_message_contains,
)
from support.prompt_conditioned_llm import PromptConditionedProvider
from yuubot.actor.prompt_docs import ADMIN_PAGES_INTRO, ADMIN_PAGES_SUBMIT_FLOW

RulePredicate = Callable[[LLMInput], bool]
RuleBuilder = Callable[[LLMInput], list[StreamEvent]]

SOURCE_PAGE = "projects/q3-review/approval-form.html"
WAKEUP_DOC = "projects/q3-review/wakeup.md"
KV_KEY = "forms/q3-approval/draft"
CONVERSATION_ID = "dynamic-page-c1"
PURPOSE = "Q3 budget approval for ACME renewal ACME-42"

APPROVAL_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Q3 Approval</title>
</head>
<body>
  <form id="approval-form">
    <input name="request_id" value="ACME-42">
    <input name="amount" type="number">
    <select name="decision"><option value="approve">Approve</option><option value="reject">Reject</option></select>
    <textarea name="reason"></textarea>
    <button type="submit">Submit</button>
  </form>
  <script>
    const PAGE_META = {{
      source_page: "{SOURCE_PAGE}",
      purpose: "{PURPOSE}",
      kv_key: "{KV_KEY}",
      conversation_id: "{CONVERSATION_ID}",
    }};
    const actorId = "amy";
    async function loadDraft() {{
      const res = await fetch(`/api/actors/${{actorId}}/kv/${{encodeURIComponent(PAGE_META.kv_key)}}`);
      return res.ok ? (await res.json()).value : {{}};
    }}
    async function submitForm(payload) {{
      await fetch(`/api/actors/${{actorId}}/kv/${{encodeURIComponent(PAGE_META.kv_key)}}`, {{
        method: "PUT",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ value: payload }}),
      }});
      await fetch(`/api/actors/${{actorId}}/inbound`, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{
          text: JSON.stringify({{
            kind: "html_form_submit",
            submitted_at: new Date().toISOString(),
            source_page: PAGE_META.source_page,
            purpose: PAGE_META.purpose,
            kv_key: PAGE_META.kv_key,
            payload,
          }}),
          conversation_id: PAGE_META.conversation_id,
          source: {{ kind: "html_form_submit", page: PAGE_META.source_page, kv_key: PAGE_META.kv_key }},
        }}),
      }});
    }}
    document.getElementById("approval-form").addEventListener("submit", async (event) => {{
      event.preventDefault();
      await submitForm(Object.fromEntries(new FormData(event.currentTarget).entries()));
    }});
  </script>
</body>
</html>
"""

WAKEUP_MARKDOWN = f"""# Wakeup context

- source_page: {SOURCE_PAGE}
- purpose: {PURPOSE}
- kv_key: {KV_KEY}
- final_rule: approve only ACME-42 submissions at or below 125000 when the form decision is approve.
"""


def _tool_call_contains(name: str, text: str) -> RulePredicate:
    def matches(inp: LLMInput) -> bool:
        return any(isinstance(item, GenToolCall) and item.name == name and text in item.arguments for item in inp.messages)

    return matches


def _tool_result_contains(tool_name: str, text: str) -> RulePredicate:
    def matches(inp: LLMInput) -> bool:
        tool_call_ids = [item.id for item in inp.messages if isinstance(item, GenToolCall) and item.name == tool_name]
        for item in inp.messages:
            if isinstance(item, ToolResult) and item.tool_call_id in tool_call_ids:
                if any(content.kind == "text" and text in content.text for content in item.content):
                    return True
        return False

    return matches


def _call_tools(calls: list[tuple[str, str, dict[str, object]]]) -> RuleBuilder:
    def build(inp: LLMInput) -> list[StreamEvent]:
        del inp
        events: list[StreamEvent] = []
        for call_id, name, args in calls:
            events.extend(
                [
                    StreamEvent(group_id=call_id, kind="tool_name", payload={"id": call_id, "name": name}),
                    StreamEvent(group_id=call_id, kind="tool_arguments_delta", payload={"text": json.dumps(args)}),
                    StreamEvent(group_id=call_id, kind="tool_arguments_end"),
                ]
            )
        events.append(StreamEvent(group_id="stop", kind="stream_stop", payload={"reason": "tool_calls"}))
        return events

    return build


def _approval_html(actor_id: str, conversation_id: str) -> str:
    return APPROVAL_HTML.replace('const actorId = "amy";', f'const actorId = "{actor_id}";').replace(
        f'conversation_id: "{CONVERSATION_ID}"',
        f'conversation_id: "{conversation_id}"',
    )


def _dynamic_page_llm(approval_html: str) -> PromptConditionedProvider:
    dynamic_page_guidance = all_of(
        prompt_contains(ADMIN_PAGES_INTRO),
        prompt_contains(ADMIN_PAGES_SUBMIT_FLOW),
    )
    return PromptConditionedProvider(
        rules=[
            (
                all_of(
                    dynamic_page_guidance,
                    user_message_contains("html_form_submit"),
                    _tool_result_contains("read", "final_rule: approve only ACME-42"),
                ),
                reply_text("FINAL_DECISION approved ACME-42 for 120000 after reading wakeup context and submitted payload."),
            ),
            (
                all_of(
                    dynamic_page_guidance,
                    has_tool_spec("read"),
                    user_message_contains("html_form_submit"),
                    user_message_contains(KV_KEY),
                ),
                call_tool("read", {"path": WAKEUP_DOC}),
            ),
            (
                all_of(
                    dynamic_page_guidance,
                    user_message_contains("build approval form"),
                    messages_contain_tool_result("write"),
                    _tool_call_contains("write", WAKEUP_DOC),
                ),
                reply_text(f"FORM_READY {SOURCE_PAGE}"),
            ),
            (
                all_of(dynamic_page_guidance, has_tool_spec("write"), user_message_contains("build approval form")),
                _call_tools(
                    [
                        ("call-write-html", "write", {"path": SOURCE_PAGE, "content": approval_html}),
                        ("call-write-wakeup", "write", {"path": WAKEUP_DOC, "content": WAKEUP_MARKDOWN}),
                    ]
                ),
            ),
        ]
    )


async def test_http_dynamic_html_form_kv_and_inbound_wakeup_flow(test_context: SharedTestContext) -> None:
    conversation_id = test_context.conversation_id("dynamic-page-c1")
    approval_html = _approval_html(test_context.actor_id, conversation_id)
    actor_id = await test_context.setup_actor(
        _dynamic_page_llm(approval_html),
        enable=False,
    )
    await enable_actor(test_context.server, actor_id)
    await ws_conversation_send(
        test_context.server,
        command_id="m1",
        actor_id=actor_id,
        conversation_id=conversation_id,
        content="build approval form for ACME-42",
    )
    history = await conversation_history(test_context.server, conversation_id)
    assert history[-1]["payload"] == {"text": f"FORM_READY {SOURCE_PAGE}"}

    page = test_context.workspace / SOURCE_PAGE
    wakeup = test_context.workspace / WAKEUP_DOC
    assert page.read_text(encoding="utf-8") == approval_html
    assert wakeup.read_text(encoding="utf-8") == WAKEUP_MARKDOWN

    url = base_url(test_context.server)
    async with httpx.AsyncClient() as client:
        opened = await client.get(f"{url}/api/actors/{actor_id}/files/{SOURCE_PAGE}", timeout=10.0)
    assert opened.status_code == 200
    assert f'kv_key: "{KV_KEY}"' in opened.text

    payload = {
        "request_id": "ACME-42",
        "amount": 120000,
        "decision": "approve",
        "reason": "Within renewal budget and below the wakeup threshold.",
    }
    await http_json("PUT", f"{url}/api/actors/{actor_id}/kv/{quote(KV_KEY, safe='')}", {"value": payload})
    inbound_text = json.dumps(
        {
            "kind": "html_form_submit",
            "submitted_at": "2026-07-03T09:00:00Z",
            "source_page": SOURCE_PAGE,
            "purpose": PURPOSE,
            "kv_key": KV_KEY,
            "payload": payload,
        }
    )
    await http_json(
        "POST",
        f"{url}/api/actors/{actor_id}/inbound",
        {
            "text": inbound_text,
            "conversation_id": conversation_id,
            "source": {"kind": "html_form_submit", "page": SOURCE_PAGE, "kv_key": KV_KEY},
        },
    )
    history = await wait_for_history_kind(test_context.server, conversation_id, "gen_text")
    while not str(cast(JsonObject, history[-1]["payload"]).get("text", "")).startswith("FINAL_DECISION"):
        history = await wait_for_history_kind(test_context.server, conversation_id, "gen_text")

    assert cast(JsonObject, history[-1]["payload"])["text"] == (
        "FINAL_DECISION approved ACME-42 for 120000 after reading wakeup context and submitted payload."
    )
