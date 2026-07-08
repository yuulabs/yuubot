"""Global skill records for progressive SOP/workflow loading."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
import shutil
from typing import Literal

import msgspec

from ..util.time import utc_now_iso

_SKILL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")


class InstalledSkillRow(msgspec.Struct, frozen=True):
    name: str
    path: str


class SkillRecord(msgspec.Struct, frozen=True):
    id: str
    name: str
    description: str = ""
    body: str = ""
    scope: str = "global"
    created_at: str = ""
    updated_at: str = ""


class SkillInput(msgspec.Struct, frozen=True):
    name: str
    description: str = ""
    body: str = ""
    scope: str = "global"

    def to_record(self, skill_id: str) -> SkillRecord:
        return SkillRecord(
            skill_id,
            self.name,
            self.description,
            self.body,
            self.scope,
        )


class SkillSummary(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    description: str = ""
    scope: str = "global"
    inspect_hint: str


SkillCliAction = Literal["add", "remove", "update"]


class SkillCliCommandBody(msgspec.Struct, frozen=True):
    action: SkillCliAction
    target: str = ""


class SkillCliCommandResult(msgspec.Struct, frozen=True):
    action: SkillCliAction
    target: str
    command: tuple[str, ...]
    exit_code: int
    stdout: str = ""
    stderr: str = ""


def validate_skill_record(record: SkillRecord) -> None:
    if not _SKILL_ID_RE.fullmatch(record.id):
        raise ValueError("skill id must be 1-80 characters of letters, numbers, dot, dash, or underscore")
    if not record.name.strip():
        raise ValueError("skill name is required")
    if record.scope != "global":
        raise ValueError("only global skills are supported in v1")


def skill_summary(record: SkillRecord) -> SkillSummary:
    return SkillSummary(
        id=record.id,
        name=record.name,
        description=record.description or _description_from_body(record.body),
        scope=record.scope,
        inspect_hint=f"await yb.skills.read({record.id!r})",
    )


def stored_skill(record: SkillRecord, existing: SkillRecord | None = None) -> SkillRecord:
    validate_skill_record(record)
    now = utc_now_iso()
    return SkillRecord(
        record.id,
        record.name.strip(),
        record.description.strip() or _description_from_body(record.body),
        record.body,
        record.scope,
        existing.created_at if existing is not None and existing.created_at else now,
        now,
    )


def _description_from_body(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:240]
    return "No description provided."


async def installed_global_skill_summaries() -> list[SkillSummary]:
    """List global skills installed by the public `skills` CLI.

    The admin UI should keep working when Node or the CLI is not available, so
    discovery failures intentionally resolve to an empty list.
    """
    command = ("npx", "-y", "skills", "ls", "-g", "--json")
    if shutil.which(command[0]) is None:
        return []
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except (OSError, TimeoutError):
        return []
    if process.returncode != 0:
        return []
    try:
        items = msgspec.json.decode(stdout, type=list[InstalledSkillRow])
    except msgspec.DecodeError:
        return []
    summaries: dict[str, SkillSummary] = {}
    for item in items:
        if not item.name or not item.path:
            continue
        summaries[item.name] = _installed_skill_summary(item.name, Path(item.path))
    return [summaries[key] for key in sorted(summaries)]


async def run_skill_cli_command(body: SkillCliCommandBody) -> SkillCliCommandResult:
    target = body.target.strip()
    command = _skill_cli_command(body.action, target)
    if shutil.which(command[0]) is None:
        raise RuntimeError("npx was not found on PATH")
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("skills command timed out after 120s") from None
    return SkillCliCommandResult(
        body.action,
        target,
        command,
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _skill_cli_command(action: SkillCliAction, target: str) -> tuple[str, ...]:
    if action == "add":
        if not target:
            raise ValueError("skill source is required")
        return ("npx", "-y", "skills", "add", "-g", "-y", target)
    if action == "remove":
        if not target:
            raise ValueError("skill name is required")
        return ("npx", "-y", "skills", "remove", "-g", "-y", target)
    if action == "update":
        if target:
            return ("npx", "-y", "skills", "update", "-g", target)
        return ("npx", "-y", "skills", "update", "-g")
    raise ValueError(f"unsupported skills action: {action}")


def _installed_skill_summary(name: str, path: Path) -> SkillSummary:
    skill_file = path / "SKILL.md"
    text = ""
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError:
        pass
    metadata = _frontmatter(text)
    display_name = metadata.get("name") or name
    description = metadata.get("description") or _description_from_body(text)
    return SkillSummary(
        id=name,
        name=display_name,
        description=description,
        scope="global",
        inspect_hint=str(skill_file),
    )


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in {"name", "description"} and value:
            values[key] = value
    return values
