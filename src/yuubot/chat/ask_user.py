"""Structured user questions that suspend and later resume a conversation."""

import msgspec

from ..domain.messages import ContentItem, GenToolCall, HistoryItem, ToolResult
from ..domain.stream import ToolCall

ASK_USER_NAME = "ask_user"
ASK_USER_DESCRIPTION = (
    "Pause the conversation to ask the user one or more questions in the web UI. "
    "Call this tool by itself, never in parallel with other tools. The user may "
    "choose a suggested option or enter a free-form answer."
)


class AskUserOption(msgspec.Struct, frozen=True):
    label: str
    description: str = ""


class AskUserQuestion(msgspec.Struct, frozen=True):
    id: str
    question: str
    header: str = ""
    options: list[AskUserOption] = msgspec.field(default_factory=list)


class AskUserPayload(msgspec.Struct, frozen=True):
    questions: list[AskUserQuestion]


class AskUserAnswer(msgspec.Struct, frozen=True):
    id: str
    answer: str


ASK_USER_TOOL_SPEC: dict[str, object] = {
    "type": "function",
    "function": {
        "name": ASK_USER_NAME,
        "description": ASK_USER_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "Questions to show together. Every question id must be unique.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "minLength": 1},
                            "header": {"type": "string"},
                            "question": {"type": "string", "minLength": 1},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string", "minLength": 1},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["label"],
                                },
                            },
                        },
                        "required": ["id", "question"],
                    },
                }
            },
            "required": ["questions"],
        },
    },
}


def decode_questions(call: GenToolCall | ToolCall) -> AskUserPayload:
    payload = msgspec.json.decode(
        (call.arguments or "{}").encode(), type=AskUserPayload
    )
    if not payload.questions:
        raise ValueError("ask_user requires at least one question")
    ids: set[str] = set()
    for question in payload.questions:
        if not question.id.strip() or not question.question.strip():
            raise ValueError("ask_user question id and text must be non-empty")
        if question.id in ids:
            raise ValueError(f"duplicate ask_user question id: {question.id}")
        ids.add(question.id)
    return payload


def pending_ask_user(items: list[HistoryItem]) -> GenToolCall | None:
    answered: set[str] = {
        item.tool_call_id for item in items if isinstance(item, ToolResult)
    }
    for item in reversed(items):
        if (
            isinstance(item, GenToolCall)
            and item.name == ASK_USER_NAME
            and item.id not in answered
        ):
            return item
    return None


def answer_result(
    call: GenToolCall, answers: list[AskUserAnswer], skipped: bool
) -> ToolResult:
    payload: dict[str, object]
    if skipped:
        payload = {"status": "skipped"}
    else:
        payload = {
            "status": "answered",
            "answers": [msgspec.to_builtins(answer) for answer in answers],
        }
    return ToolResult(
        tool_call_id=call.id,
        content=[ContentItem("text", msgspec.json.encode(payload).decode())],
    )
