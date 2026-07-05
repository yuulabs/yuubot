from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import cast

from yuubot.domain import (
    GenToolCall,
    InputMessage,
    LLMInput,
    StreamEvent,
    ToolResult,
)

RulePredicate = Callable[[LLMInput], bool]
RuleBuilder = Callable[[LLMInput], list[StreamEvent]]


def developer_messages(inp: LLMInput) -> list[InputMessage]:
    return [item for item in inp.messages if isinstance(item, InputMessage) and item.role == "developer"]


def developer_text(inp: LLMInput) -> str:
    parts: list[str] = []
    for message in developer_messages(inp):
        for item in message.content:
            if item.kind == "text":
                parts.append(item.text)
    return "\n".join(parts)


def user_messages(inp: LLMInput) -> list[InputMessage]:
    return [item for item in inp.messages if isinstance(item, InputMessage) and item.role == "user"]


def last_user_message(inp: LLMInput) -> InputMessage | None:
    messages = user_messages(inp)
    return messages[-1] if messages else None


def prompt_contains(text: str) -> RulePredicate:
    def matches(inp: LLMInput) -> bool:
        return text in developer_text(inp)

    return matches


def has_tool_spec(name: str) -> RulePredicate:
    def matches(inp: LLMInput) -> bool:
        for spec in inp.tool_specs:
            function = spec.get("function")
            if isinstance(function, dict) and function.get("name") == name:
                return True
        return False

    return matches


def has_tool_specs(*names: str) -> RulePredicate:
    expected = set(names)

    def matches(inp: LLMInput) -> bool:
        return set(tool_names(inp)) == expected

    return matches


def messages_contain_tool_result(tool_name: str) -> RulePredicate:
    def matches(inp: LLMInput) -> bool:
        for index, item in enumerate(inp.messages):
            if not isinstance(item, GenToolCall) or item.name != tool_name:
                continue
            for later in inp.messages[index + 1 :]:
                if isinstance(later, ToolResult) and later.tool_call_id == item.id:
                    return True
        return False

    return matches


def runtime_developer_notice(keyword: str) -> RulePredicate:
    """Match developer notices injected during a run, excluding the seeded system prompt."""

    def matches(inp: LLMInput) -> bool:
        notices = developer_messages(inp)[1:]
        for message in notices:
            for item in message.content:
                if item.kind == "text" and keyword in item.text:
                    return True
        return False

    return matches


def integration_sdk_documented(package_path: str) -> RulePredicate:
    def matches(inp: LLMInput) -> bool:
        return f"{package_path}:\n" in developer_text(inp)

    return matches


def user_message_contains(text: str) -> RulePredicate:
    def matches(inp: LLMInput) -> bool:
        message = last_user_message(inp)
        if message is None:
            return False
        return any(item.kind == "text" and text in item.text for item in message.content)

    return matches


def user_message_has_text_and_path(text: str, path: str) -> RulePredicate:
    def matches(inp: LLMInput) -> bool:
        message = last_user_message(inp)
        if message is None:
            return False
        has_text = any(item.kind == "text" and item.text == text for item in message.content)
        has_path = any(item.kind == "file" and item.path == path for item in message.content)
        return has_text and has_path

    return matches


def all_of(*predicates: RulePredicate) -> RulePredicate:
    def matches(inp: LLMInput) -> bool:
        return all(predicate(inp) for predicate in predicates)

    return matches


def call_tool(name: str, args: dict[str, object], *, call_id: str | None = None) -> RuleBuilder:
    tool_call_id = call_id or f"call-{uuid.uuid4().hex[:8]}"

    def build(inp: LLMInput) -> list[StreamEvent]:
        del inp
        return [
            StreamEvent(group_id=tool_call_id, kind="tool_name", payload={"id": tool_call_id, "name": name}),
            StreamEvent(group_id=tool_call_id, kind="tool_arguments_delta", payload={"text": json.dumps(args)}),
            StreamEvent(group_id=tool_call_id, kind="tool_arguments_end"),
            StreamEvent(group_id="stop", kind="stream_stop", payload={"reason": "tool_calls"}),
        ]

    return build


def reply_text(text: str, *, usage: dict[str, object] | None = None) -> RuleBuilder:
    def build(inp: LLMInput) -> list[StreamEvent]:
        del inp
        payload: dict[str, object] = {"reason": "stop"}
        if usage is not None:
            payload["usage"] = usage
        return [
            StreamEvent(group_id="text-1", kind="text_delta", payload={"text": text}),
            StreamEvent(group_id="stop", kind="stream_stop", payload=payload),
        ]

    return build


def reply_blocked(reason: str = "length") -> RuleBuilder:
    def build(inp: LLMInput) -> list[StreamEvent]:
        del inp
        return [
            StreamEvent(group_id="text-1", kind="text_delta", payload={"text": "partial"}),
            StreamEvent(group_id="stop", kind="stream_stop", payload={"reason": reason}),
        ]

    return build


def tool_names(inp: LLMInput) -> list[str]:
    names: list[str] = []
    for spec in inp.tool_specs:
        function = spec.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def message_kinds(inp: LLMInput) -> list[str]:
    kinds: list[str] = []
    for item in inp.messages:
        if isinstance(item, InputMessage):
            kinds.append(f"input:{item.role}")
        elif isinstance(item, GenToolCall):
            kinds.append(f"gen_tool_call:{item.name}")
        elif isinstance(item, ToolResult):
            kinds.append("tool_result")
        else:
            kinds.append(type(item).__name__)
    return kinds


def cast_messages(inp: LLMInput) -> list[object]:
    return cast(list[object], inp.messages)
