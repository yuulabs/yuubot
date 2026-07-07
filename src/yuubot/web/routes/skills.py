"""Skill admin routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...runtime.skills import SkillCliCommandBody, SkillInput, skill_summary
from ..request import bad_request, read_json
from ..responses import error_response, json_response


def register_skill_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/skills")
    async def api_skills() -> Response:
        return json_response({"items": app.skill_summaries()})

    @api.get("/api/skills/installed")
    async def api_installed_skills() -> Response:
        return json_response({"items": await app.installed_skill_summaries()})

    @api.post("/api/skills/commands")
    async def api_skill_command(request: Request) -> Response:
        try:
            body = await read_json(request, SkillCliCommandBody)
            result = await app.run_skill_command(body)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        except RuntimeError as exc:
            return error_response(422, "skills_command_failed", str(exc))
        return json_response(result)

    @api.get("/api/skills/{skill_id}")
    async def api_skill(skill_id: str) -> Response:
        record = app.runtime.skills.get(skill_id)
        if record is None:
            return error_response(404, "not_found", "skill not found")
        return json_response(record)

    @api.put("/api/skills/{skill_id}")
    async def api_put_skill(skill_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, SkillInput)
            record = await app.put_skill(body.to_record(skill_id))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        return json_response({"record": record, "summary": skill_summary(record)})

    @api.delete("/api/skills/{skill_id}")
    async def api_delete_skill(skill_id: str) -> Response:
        if not await app.delete_skill(skill_id):
            return error_response(404, "not_found", "skill not found")
        return json_response({"id": skill_id, "deleted": True})
