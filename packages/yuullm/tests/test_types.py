from __future__ import annotations

import msgspec

import yuullm
from yuullm.types import is_tool_call_item, with_last_item_cache_control


def test_message_helpers_create_struct_messages() -> None:
    message = yuullm.user("hello", name="turn-1")

    assert isinstance(message, yuullm.Message)
    assert message.role == "user"
    assert message.content == [{"type": "text", "text": "hello"}]
    assert message.provider_extra == {"name": "turn-1"}


def test_message_content_type_aliases_cover_content_and_protocol_items() -> None:
    tool_call = yuullm.ToolCall(id="call_1", name="search", arguments='{"q":"x"}')
    assistant_message = yuullm.assistant("checking", yuullm.tool_call_item(tool_call))
    tool_message = yuullm.tool("call_1", [{"type": "text", "text": "done"}])

    assert yuullm.render_message_text(assistant_message) == 'checkingsearch({"q":"x"})'
    assert yuullm.render_message_text(tool_message) == "done"
    item = assistant_message.content[1]
    assert is_tool_call_item(item)
    assert yuullm.tool_arguments(item) == {"q": "x"}


def test_history_can_contain_tool_specs() -> None:
    specs = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search.",
                "parameters": {"type": "object"},
            },
        }
    ]
    history = [yuullm.tools(specs), yuullm.system("sys"), yuullm.user("hello")]

    messages, tools = yuullm.split_history(history)

    assert tools == specs
    assert [message.role for message in messages] == ["system", "user"]


def test_with_last_item_cache_control_preserves_message_shape() -> None:
    message = yuullm.user("stable", id="prefix")
    cached = with_last_item_cache_control(message, {"type": "ephemeral", "ttl": 3600})

    assert cached is not message
    assert cached.role == "user"
    assert cached.provider_extra == {"id": "prefix"}
    assert cached.content[-1]["cache_control"] == {"type": "ephemeral", "ttl": 3600}
    assert "cache_control" not in message.content[-1]


def test_tick_carries_optional_partial_tool_call_payload() -> None:
    # Default tick stays payload-less for backward compatibility.
    bare = yuullm.Tick()
    assert bare.partial_tool_call is None

    # A partial-tool-call tick forwards name (and id when known) to consumers
    # before the complete ToolCall lands.
    partial = yuullm.PartialToolCall(name="edit", id="call_1")
    tick = yuullm.Tick(partial_tool_call=partial)
    assert tick.partial_tool_call is not None
    assert tick.partial_tool_call.name == "edit"
    assert tick.partial_tool_call.id == "call_1"

    # Round-trips via msgspec for downstream serialization boundaries.
    decoded = msgspec.json.decode(
        msgspec.json.encode(tick), type=yuullm.Tick
    )
    assert decoded.partial_tool_call is not None
    assert decoded.partial_tool_call.name == "edit"
