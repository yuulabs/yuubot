from __future__ import annotations

from typing import cast

HistoryRecord = dict[str, object]

PREFIX_KINDS = frozenset({"tool_specs", "system_prompt"})


def payload(record: HistoryRecord) -> HistoryRecord:
    return cast(HistoryRecord, record["payload"])


def text_content(record: HistoryRecord) -> str:
    content = cast(list[HistoryRecord], payload(record)["content"])
    text = content[0]["text"]
    if not isinstance(text, str):
        raise TypeError("expected text content")
    return text


def interaction_kinds(history: list[HistoryRecord]) -> list[str]:
    return [str(item["kind"]) for item in history]


def prefix_kinds(history: list[HistoryRecord]) -> list[str]:
    return [str(item["kind"]) for item in history if str(item["kind"]) in PREFIX_KINDS]


def tool_result_text(history: list[HistoryRecord], index: int = -1) -> str:
    tool_results = [item for item in history if item["kind"] == "tool_result"]
    if not tool_results:
        raise AssertionError("expected at least one tool_result")
    return text_content(tool_results[index])


def runtime_developer_notice_count(history: list[HistoryRecord], keyword: str) -> int:
    developer_inputs = [
        item for item in history if item["kind"] == "input" and payload(item).get("role") == "developer"
    ]
    return sum(1 for item in developer_inputs if keyword in text_content(item))
