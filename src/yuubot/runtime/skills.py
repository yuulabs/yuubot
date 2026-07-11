"""Unified global skill catalog and Actor workspace copies."""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
import re
import shutil
import tempfile
from typing import Literal

import msgspec

from ..util.time import utc_now_iso

_SKILL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
SkillSource = Literal["builtin", "custom", "package"]
_discovery_cache: tuple[list[SkillRecord] | None, str] | None = None


class SkillRecord(msgspec.Struct, frozen=True):
    id: str
    name: str
    description: str = ""
    body: str = ""
    scope: str = "global"
    created_at: str = ""
    updated_at: str = ""
    source: SkillSource = "custom"
    source_path: str = ""


class SkillInput(msgspec.Struct, frozen=True):
    name: str
    description: str = ""
    body: str = ""
    scope: str = "global"

    def to_record(self, skill_id: str) -> SkillRecord:
        return SkillRecord(skill_id, self.name, self.description, self.body, self.scope)


class SkillCreateInput(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    id: str
    name: str
    description: str = ""
    body: str = ""

    def to_record(self) -> SkillRecord:
        return SkillRecord(self.id, self.name, self.description, self.body)


class SkillSummary(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    description: str = ""
    scope: str = "global"
    inspect_hint: str
    source: SkillSource
    can_edit: bool
    can_update: bool
    can_delete: bool
    can_copy: bool = True
    error: str = ""


class WorkspaceSkillSummary(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    description: str
    loaded: bool
    path: str


class WorkspaceSkillLoadedBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    loaded: bool


class SkillSearchResult(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    description: str
    source: str
    loaded: bool | None
    inspect_hint: str


class SkillCopyFile(msgspec.Struct, frozen=True):
    path: str
    status: Literal["added", "deleted", "modified", "unchanged"]
    binary: bool = False
    diff: str = ""


class SkillCopyPreview(msgspec.Struct, frozen=True):
    skill_id: str
    actor_id: str
    path: str
    exists: bool
    conflict: bool
    up_to_date: bool
    files: tuple[SkillCopyFile, ...] = ()


class SkillCopyBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    actor_id: str
    replace: bool = False


class SkillPackageBody(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    source: str
    skills: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    copy: bool = False


SkillPackageAction = Literal["add", "remove", "update"]


class SkillPackageResult(msgspec.Struct, frozen=True):
    action: SkillPackageAction
    target: str
    command: tuple[str, ...]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    warning: str = ""


def validate_skill_record(record: SkillRecord) -> None:
    if not _SKILL_ID_RE.fullmatch(record.id):
        raise ValueError("skill id must be 1-80 characters of letters, numbers, dot, dash, or underscore")
    if not record.name.strip():
        raise ValueError("skill name is required")
    if record.scope != "global":
        raise ValueError("only global skills are supported")


def skill_summary(record: SkillRecord, error: str = "") -> SkillSummary:
    usable = not error
    return SkillSummary(
        id=record.id,
        name=record.name,
        description=record.description or _description_from_body(record.body),
        scope=record.scope,
        inspect_hint=f"await yb.skills.read({record.id!r})" if usable else "",
        source=record.source,
        can_edit=usable and record.source in {"builtin", "custom"},
        can_update=usable and record.source == "package",
        can_delete=True,
        can_copy=usable,
        error=error,
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
        existing.source if existing is not None else record.source,
        existing.source_path if existing is not None else record.source_path,
    )


def builtin_skill_records() -> list[SkillRecord]:
    root = Path(__file__).resolve().parent.parent / "builtin_skills"
    records: list[SkillRecord] = []
    for path in sorted(root.glob("*/SKILL.md")):
        body = path.read_text(encoding="utf-8")
        metadata = _frontmatter(body)
        skill_id = path.parent.name
        records.append(
            SkillRecord(
                skill_id,
                metadata.get("name", skill_id),
                metadata.get("description", _description_from_body(body)),
                body,
                source="builtin",
                source_path=str(path.parent),
            )
        )
    return records


def resolve_catalog(
    managed: list[SkillRecord], packages: list[SkillRecord]
) -> tuple[dict[str, SkillRecord], list[SkillSummary]]:
    grouped: dict[str, list[SkillRecord]] = {}
    for record in [*managed, *packages]:
        grouped.setdefault(record.id, []).append(record)
    usable: dict[str, SkillRecord] = {}
    items: list[SkillSummary] = []
    for skill_id in sorted(grouped):
        records = grouped[skill_id]
        if len(records) == 1:
            usable[skill_id] = records[0]
            items.append(skill_summary(records[0]))
            continue
        sources = ", ".join(record.source for record in records)
        error = f"Duplicate skill ID '{skill_id}' from: {sources}. Rename or remove one source."
        items.extend(skill_summary(record, error) for record in records)
    return usable, items


async def discover_package_skills(force: bool = False) -> tuple[list[SkillRecord] | None, str]:
    global _discovery_cache
    if not force and _discovery_cache is not None:
        records, warning = _discovery_cache
        return list(records) if records is not None else None, warning
    root = Path.home() / ".agents" / "skills"
    try:
        paths = sorted(root.glob("*/SKILL.md")) if root.exists() else []
    except OSError as exc:
        result = (None, f"Package discovery failed: {exc}")
        _discovery_cache = _discovery_cache or result
        return result
    records: list[SkillRecord] = []
    warnings: list[str] = []
    for skill_file in paths:
        skill_root = skill_file.parent
        skill_id = skill_root.name
        try:
            body = skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"{skill_id}: {exc}")
            continue
        metadata = _frontmatter(body)
        record = SkillRecord(
            skill_id,
            metadata.get("name") or skill_id,
            metadata.get("description") or _description_from_body(body),
            body,
            source="package",
            source_path=str(skill_root),
        )
        try:
            validate_skill_record(record)
        except ValueError as exc:
            warnings.append(f"{skill_id}: {exc}")
            continue
        records.append(record)
    warning = "Package discovery skipped invalid entries: " + "; ".join(warnings) if warnings else ""
    _discovery_cache = (list(records), warning)
    return records, warning


async def run_package_command(
    action: SkillPackageAction, target: str = "", body: SkillPackageBody | None = None
) -> SkillPackageResult:
    command = _package_command(action, target, body)
    if shutil.which(command[0]) is None:
        raise RuntimeError("npx was not found on PATH")
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("skills command timed out after 120s") from None
    result = SkillPackageResult(
        action,
        target,
        command,
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )
    if result.exit_code != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"skills exited with {result.exit_code}")
    return result


def _package_command(
    action: SkillPackageAction, target: str, body: SkillPackageBody | None
) -> tuple[str, ...]:
    if action == "add":
        if body is None or not body.source.strip():
            raise ValueError("skill source is required")
        args: list[str] = ["npx", "-y", "skills", "add", body.source.strip()]
        for skill in body.skills:
            if skill.strip():
                args.extend(("--skill", skill.strip()))
        for agent in body.agents:
            if agent.strip():
                args.extend(("--agent", agent.strip()))
        if body.copy:
            args.append("--copy")
        return (*args, "--global", "--yes")
    if action == "remove":
        if not target.strip():
            raise ValueError("skill id is required")
        return ("npx", "-y", "skills", "remove", target.strip(), "--global", "--yes")
    if action == "update":
        middle = (target.strip(),) if target.strip() else ()
        return ("npx", "-y", "skills", "update", *middle, "--global", "--yes")
    raise ValueError(f"unsupported package action: {action}")


def skill_copy_preview(record: SkillRecord, actor_id: str, workspace: Path) -> SkillCopyPreview:
    validate_skill_record(record)
    target = workspace.resolve() / ".agents" / "skills" / record.id
    if target.is_symlink():
        raise ValueError(f"skill target may not be a symlink: {target}")
    source_files = _source_files(record)
    target_files = _tree_files(target) if target.exists() else {}
    files: list[SkillCopyFile] = []
    for relative in sorted(source_files.keys() | target_files.keys()):
        source = source_files.get(relative)
        current = target_files.get(relative)
        if current is None:
            files.append(SkillCopyFile(relative, "added", _is_binary(source or b"")))
        elif source is None:
            files.append(SkillCopyFile(relative, "deleted", _is_binary(current)))
        elif source == current:
            files.append(SkillCopyFile(relative, "unchanged", _is_binary(source)))
        else:
            binary = _is_binary(source) or _is_binary(current)
            diff = "" if binary else _text_diff(relative, current, source)
            files.append(SkillCopyFile(relative, "modified", binary, diff))
    changed = any(item.status != "unchanged" for item in files)
    exists = target.exists()
    return SkillCopyPreview(
        record.id,
        actor_id,
        f".agents/skills/{record.id}",
        exists,
        exists and changed,
        exists and not changed,
        tuple(files),
    )


def copy_skill(record: SkillRecord, actor_id: str, workspace: Path, replace: bool) -> SkillCopyPreview:
    preview = skill_copy_preview(record, actor_id, workspace)
    if preview.up_to_date:
        return preview
    if preview.exists and not replace:
        raise FileExistsError(f"workspace skill already exists: {preview.path}")
    target = workspace.resolve() / preview.path
    target.parent.mkdir(parents=True, exist_ok=True)
    source_files = _source_files(record)
    staging = Path(tempfile.mkdtemp(prefix=f".{record.id}-", dir=target.parent))
    backup = target.parent / f".{record.id}-backup"
    try:
        for relative, content in source_files.items():
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        if target.exists():
            if backup.exists():
                shutil.rmtree(backup)
            target.rename(backup)
        staging.rename(target)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if not target.exists() and backup.exists():
            backup.rename(target)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return skill_copy_preview(record, actor_id, workspace)


def workspace_skills(workspace: Path) -> list[WorkspaceSkillSummary]:
    root = workspace.resolve() / ".agents" / "skills"
    items: list[WorkspaceSkillSummary] = []
    for path in sorted(root.glob("*/SKILL.md")):
        body = path.read_text(encoding="utf-8")
        metadata = _frontmatter(body)
        skill_id = path.parent.name
        items.append(
            WorkspaceSkillSummary(
                id=skill_id,
                name=metadata.get("name") or skill_id,
                description=metadata.get("description") or _description_from_body(body),
                loaded=metadata.get("loaded", "true").strip().lower() not in {"false", "no", "0"},
                path=str(path),
            )
        )
    return items


def set_workspace_skill_loaded(workspace: Path, skill_id: str, loaded: bool) -> WorkspaceSkillSummary:
    if not _SKILL_ID_RE.fullmatch(skill_id):
        raise ValueError("invalid skill id")
    path = workspace.resolve() / ".agents" / "skills" / skill_id / "SKILL.md"
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(skill_id)
    body = path.read_text(encoding="utf-8")
    path.write_text(_set_loaded_text(body, loaded), encoding="utf-8")
    return next(item for item in workspace_skills(workspace) if item.id == skill_id)


def search_skills(
    query: str, limit: int, catalog: list[SkillRecord], workspace: Path | None = None
) -> list[SkillSearchResult]:
    terms = [term.casefold() for term in query.split() if term]
    if not terms:
        return []
    candidates: list[tuple[tuple[int, int, str], SkillSearchResult]] = []
    for record in catalog:
        fields = (record.id, record.name, record.description, record.body)
        score = _skill_search_score(terms, fields)
        if score is not None:
            candidates.append(((*score, record.id), SkillSearchResult(
                id=record.id, name=record.name, description=record.description or _description_from_body(record.body),
                source="global", loaded=None, inspect_hint=f"await yb.skills.read({record.id!r}); copy it to the Actor workspace to load it in the prompt",
            )))
    if workspace is not None:
        for item in workspace_skills(workspace):
            body = Path(item.path).read_text(encoding="utf-8")
            score = _skill_search_score(terms, (item.id, item.name, item.description, body))
            if score is not None:
                candidates.append(((*score, item.id), SkillSearchResult(
                    id=item.id, name=item.name, description=item.description, source="workspace", loaded=item.loaded,
                    inspect_hint=f"Use the read tool on `{item.path}`",
                )))
    candidates.sort(key=lambda pair: pair[0])
    return [item for _, item in candidates[:max(1, min(limit, 10))]]


def _skill_search_score(terms: list[str], fields: tuple[str, str, str, str]) -> tuple[int, int] | None:
    lowered = tuple(field.casefold() for field in fields)
    ranks = [next((index for index, field in enumerate(lowered) if term in field), 99) for term in terms]
    if 99 in ranks:
        return None
    return max(ranks), sum(ranks)


def _source_files(record: SkillRecord) -> dict[str, bytes]:
    if record.source_path:
        files = _tree_files(Path(record.source_path))
        skill_body = record.body if record.source == "builtin" else files.get("SKILL.md", record.body.encode()).decode("utf-8")
        files["SKILL.md"] = (_set_loaded_text(skill_body, True).rstrip() + "\n").encode()
        return files
    body = record.body
    if not body.startswith("---\n"):
        body = f"---\nname: {record.name}\ndescription: {record.description}\n---\n{body}"
    return {"SKILL.md": (_set_loaded_text(body, True).rstrip() + "\n").encode()}


def _set_loaded_text(body: str, loaded: bool) -> str:
    lines = body.splitlines(keepends=True)
    value = f"loaded: {'true' if loaded else 'false'}\n"
    if lines and lines[0].strip() == "---":
        end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if end is None:
            raise ValueError("skill frontmatter is not closed")
        found = next((index for index in range(1, end) if lines[index].partition(":")[0].strip() == "loaded"), None)
        if found is None:
            lines.insert(end, value)
        else:
            lines[found] = value
        return "".join(lines)
    return f"---\nloaded: {'true' if loaded else 'false'}\n---\n" + body


def _tree_files(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    if root.is_symlink():
        raise ValueError(f"skill root may not be a symlink: {root}")
    resolved_root = root.resolve()
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not _is_within(resolved, resolved_root):
                raise ValueError(f"unsafe skill symlink escapes root: {path}")
            if resolved.is_dir():
                raise ValueError(f"skill directory symlinks are not supported: {path}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_binary(content: bytes) -> bool:
    if b"\0" in content:
        return True
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _text_diff(path: str, current: bytes, source: bytes) -> str:
    return "".join(
        difflib.unified_diff(
            current.decode().splitlines(keepends=True),
            source.decode().splitlines(keepends=True),
            fromfile=f"workspace/{path}",
            tofile=f"global/{path}",
        )
    )


def _description_from_body(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped != "---" and ":" not in stripped:
            return stripped[:240]
    return "No description provided."


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() in {"name", "description", "loaded"} and value.strip():
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values
