"""Unified skill catalog admin routes."""

from __future__ import annotations

import msgspec
from fastapi import FastAPI, Request
from fastapi.responses import Response

from ...app import Yuubot
from ...runtime.skills import SkillCopyBody, SkillCreateInput, SkillInput, SkillPackageBody, search_skills, skill_summary
from ..request import bad_request, read_json
from ..responses import error_response, json_response


def register_skill_routes(api: FastAPI, app: Yuubot) -> None:
    @api.get("/api/skills")
    async def api_skills() -> Response:
        return json_response(
            {"items": app.skill_catalog(), "warning": app.runtime.skill_discovery_warning}
        )

    @api.post("/api/skills/refresh")
    async def api_refresh_skills() -> Response:
        warning = await app.refresh_package_skills()
        return json_response({"items": app.skill_catalog(), "warning": warning})

    @api.get("/api/skills/search")
    async def api_search_skills(query: str, limit: int = 5, actor_id: str = "") -> Response:
        workspace = app.actor_workspace_path(actor_id) if actor_id else None
        if actor_id and workspace is None:
            return error_response(404, "not_found", "actor not found")
        records = [*app.runtime.skills.values(), *app.runtime.package_skills]
        return json_response({"items": search_skills(query, limit, records, workspace)})

    @api.post("/api/skills")
    async def api_create_skill(request: Request) -> Response:
        try:
            body = await read_json(request, SkillCreateInput)
            record = await app.create_skill(body)
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        except FileExistsError as exc:
            return error_response(409, "skill_id_conflict", str(exc))
        return json_response({"record": record, "summary": skill_summary(record)}, status=201)

    @api.post("/api/skills/packages")
    async def api_add_skill_package(request: Request) -> Response:
        try:
            body = await read_json(request, SkillPackageBody)
            return json_response(await app.add_skill_package(body))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        except RuntimeError as exc:
            return error_response(422, "skills_command_failed", str(exc))

    @api.post("/api/skills/packages/update")
    async def api_update_skill_packages() -> Response:
        try:
            return json_response(await app.update_skill_packages())
        except RuntimeError as exc:
            return error_response(422, "skills_command_failed", str(exc))

    @api.get("/api/skills/{skill_id}")
    async def api_skill(skill_id: str) -> Response:
        try:
            return json_response(app.runtime.skill_record(skill_id))
        except KeyError:
            matches = [item for item in app.skill_catalog() if item.id == skill_id]
            if matches:
                return error_response(409, "skill_id_conflict", matches[0].error)
            return error_response(404, "not_found", "skill not found")

    @api.post("/api/skills/{skill_id}/update")
    async def api_update_skill(skill_id: str) -> Response:
        try:
            return json_response(await app.update_skill_packages(skill_id))
        except KeyError:
            return error_response(404, "not_found", "skill not found")
        except ValueError as exc:
            return bad_request(exc)
        except RuntimeError as exc:
            return error_response(422, "skills_command_failed", str(exc))

    @api.get("/api/skills/{skill_id}/copy-preview")
    async def api_skill_copy_preview(skill_id: str, actor_id: str) -> Response:
        try:
            return json_response(app.skill_copy_preview(skill_id, actor_id))
        except KeyError:
            return error_response(404, "not_found", "skill or actor not found")
        except ValueError as exc:
            return error_response(422, "unsafe_skill_directory", str(exc))

    @api.post("/api/skills/{skill_id}/copy")
    async def api_skill_copy(skill_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, SkillCopyBody)
            return json_response(app.copy_skill(skill_id, body.actor_id, body.replace))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        except FileExistsError as exc:
            return error_response(409, "skill_copy_conflict", str(exc))
        except KeyError:
            return error_response(404, "not_found", "skill or actor not found")

    @api.put("/api/skills/{skill_id}")
    async def api_put_skill(skill_id: str, request: Request) -> Response:
        try:
            body = await read_json(request, SkillInput)
            record = await app.put_skill(body.to_record(skill_id))
        except (msgspec.DecodeError, msgspec.ValidationError, ValueError) as exc:
            return bad_request(exc)
        except KeyError:
            return error_response(404, "not_found", "skill not found")
        return json_response({"record": record, "summary": skill_summary(record)})

    @api.delete("/api/skills/{skill_id}")
    async def api_delete_skill(skill_id: str, source: str | None = None) -> Response:
        try:
            deleted = await app.delete_skill(skill_id, source)
        except ValueError as exc:
            return bad_request(exc)
        except RuntimeError as exc:
            return error_response(422, "skills_command_failed", str(exc))
        if not deleted:
            return error_response(404, "not_found", "skill not found")
        return json_response({"id": skill_id, "deleted": True})
