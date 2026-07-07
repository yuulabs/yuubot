"""Global skill facade for execute_python.

Use ``await list_skills()`` to see global skill summaries and
``await read(skill_id)`` to load full instructions only when needed. Skills are
workflow instructions, not data sources or credentials.
"""

from __future__ import annotations

from typing import cast

from yb._daemon import daemon_url, request_json


class SkillSummary:
    id: str
    name: str
    description: str
    scope: str
    inspect_hint: str

    def __init__(self, payload: dict[str, object]) -> None:
        self.id = str(payload.get("id", ""))
        self.name = str(payload.get("name", ""))
        self.description = str(payload.get("description", ""))
        self.scope = str(payload.get("scope", "global"))
        self.inspect_hint = str(payload.get("inspect_hint", ""))


async def list_skills() -> list[SkillSummary]:
    payload = await request_json("GET", f"{daemon_url()}/api/skills")
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    return [SkillSummary(cast(dict[str, object], item)) for item in items if isinstance(item, dict)]


async def read(skill_id: str) -> str:
    payload = await request_json("GET", f"{daemon_url()}/api/skills/{skill_id}")
    body = payload.get("body")
    return body if isinstance(body, str) else ""
