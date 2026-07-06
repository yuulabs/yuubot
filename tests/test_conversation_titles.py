from yuubot.actor.prompt import augment_user_message
from yuubot.chat.titles import title_from_user_message
from yuubot.domain.messages import InputMessage, text_content


def test_title_from_user_message_joins_text_parts() -> None:
    message = InputMessage(
        role="user",
        name="amy",
        content=[*text_content("hello"), *text_content("world")],
    )
    assert title_from_user_message(message) == "hello world"


def test_title_from_user_message_collapses_whitespace() -> None:
    message = InputMessage(role="user", name="amy", content=text_content("  hello   world  "))
    assert title_from_user_message(message) == "hello world"


def test_title_from_user_message_truncates_long_text() -> None:
    message = InputMessage(role="user", name="amy", content=text_content("a" * 100))
    assert title_from_user_message(message) == ("a" * 77 + "...")


def test_title_from_user_message_skips_empty_content() -> None:
    message = InputMessage(role="user", name="amy", content=[])
    assert title_from_user_message(message) == ""


def test_title_from_user_message_strips_real_time_context() -> None:
    message = augment_user_message(
        InputMessage(role="user", name="amy", content=text_content("daily report")),
        mode="actor",
    )
    assert title_from_user_message(message) == "daily report"
