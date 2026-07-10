"""Hosted-search rescue facades for questions beyond the current model.

Use ``await ask_gemini(prompt)`` for broad web research backed by Google-style
search. Use ``await ask_grok(prompt)`` when current X/Twitter posts are material.
Each facade can return one successful answer per user turn. Put every subquestion
needed for the result into one prompt. Calls return an independent answer plus
verified source URLs; failures do not consume the allowance.
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


async def ask_gemini(prompt: str) -> Answer:
    return await _ask("gemini", prompt)


async def ask_grok(prompt: str) -> Answer:
    return await _ask("grok", prompt)


async def _ask(facade: str, prompt: str) -> Answer:
    value = prompt.strip()
    if not value:
        raise ValueError("prompt must not be empty")
    if len(value) > 20_000:
        raise ValueError("prompt must be at most 20000 characters")

    async def request() -> Answer:
        payload = await request_json(
            "POST",
            f"{daemon_url()}/api/fixer/{facade}",
            json={"prompt": value},
            timeout_s=120,
        )
        return msgspec.convert(payload, Answer)

    return await run(f"fixer_{facade}", request)
