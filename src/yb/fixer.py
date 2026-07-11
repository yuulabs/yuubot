"""Hosted-search rescue facades for questions beyond the current model.

Research routing, from cheapest to most specialized: answer directly when sure;
use ``yext.web.search`` and then ``read`` for ordinary current facts; use
``ask_gemini(prompt)`` without web search for uncertain stable knowledge; use
``ask_grok(prompt, enable_web_search=True)`` for X/Twitter, failed ordinary
search, or blocked-page extraction; and use Gemini with web search for complex,
multi-source research. Each facade can make one provider-completed request per
user turn, so combine related subquestions.

``pass_through_options`` is only a vendor-specific escape hatch. Before passing
a non-empty value, check whether the Persona or injected AGENTS.md specifies the
field and its allowed values. If neither does, either is incomplete, or they
conflict, ask the user instead of guessing handles, dates, plugin IDs, or other
vendor parameters. Examples are structural only. ``enable_web_search`` is a
framework option and needs no such confirmation.
"""

from __future__ import annotations

import msgspec

from yb._daemon import daemon_url, request_json
from yb._turn_guard import run


class Citation(msgspec.Struct, frozen=True):
    url: str
    title: str = ""


class Answer(msgspec.Struct, frozen=True):
    text: str
    citations: list[Citation]


async def ask_gemini(
    prompt: str,
    enable_web_search: bool = False,
    pass_through_options: dict[str, object] | None = None,
) -> Answer:
    return await _ask("gemini", prompt, enable_web_search, pass_through_options)


async def ask_grok(
    prompt: str,
    enable_web_search: bool = False,
    pass_through_options: dict[str, object] | None = None,
) -> Answer:
    return await _ask("grok", prompt, enable_web_search, pass_through_options)


async def _ask(
    facade: str,
    prompt: str,
    enable_web_search: bool,
    pass_through_options: dict[str, object] | None,
) -> Answer:
    value = prompt.strip()
    if not value:
        raise ValueError("prompt must not be empty")
    if len(value) > 20_000:
        raise ValueError("prompt must be at most 20000 characters")

    async def request() -> Answer:
        payload = await request_json(
            "POST",
            f"{daemon_url()}/api/fixer/{facade}",
            json={
                "prompt": value,
                "enable_web_search": enable_web_search,
                "pass_through_options": pass_through_options,
            },
            timeout_s=120,
        )
        return msgspec.convert(payload, Answer)

    return await run(f"fixer_{facade}", request)
