"""Filesystem-backed agent skill discovery and actor-local management."""

from __future__ import annotations

from collections import OrderedDict
import shutil
from pathlib import Path
from threading import RLock

import msgspec
import yaml

from yuubot.resources.records import SkillScope

_SKILL_METADATA_CACHE_CAPACITY = 512
_SKILL_METADATA_CACHE: OrderedDict[tuple[str, int, int, bool], "_SkillMetadata"] = (
    OrderedDict()
)
_SKILL_METADATA_CACHE_LOCK = RLock()


class SkillInfo(msgspec.Struct, frozen=True):
    name: str
    source: str
    path: str
    content: str = ""
    description: str = ""


class ActorSkillsView(msgspec.Struct, frozen=True):
    global_skills: tuple[SkillInfo, ...]
    local_skills: tuple[SkillInfo, ...]
    loaded_skills: tuple[SkillInfo, ...]


def local_skills_dir(actor_workspace: Path) -> Path:
    return actor_workspace / ".agents" / "skills"


def list_skill_dirs(root: Path, *, source: str, include_content: bool) -> tuple[SkillInfo, ...]:
    if not root.exists():
        return ()
    skills: list[SkillInfo] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        metadata = _load_skill_metadata(skill_md, include_body=include_content)
        content = metadata.body if include_content else ""
        skills.append(
            SkillInfo(
                name=child.name,
                source=source,
                path=str(child),
                content=content,
                description=metadata.description,
            )
        )
    return tuple(skills)


def loaded_skills(
    *,
    global_root: Path,
    actor_workspace: Path | None,
    scope: SkillScope,
    include_content: bool,
) -> tuple[SkillInfo, ...]:
    local_root = local_skills_dir(actor_workspace) if actor_workspace is not None else None
    local = (
        list_skill_dirs(local_root, source="local", include_content=include_content)
        if local_root is not None
        else ()
    )
    if scope == "local_only":
        return local
    global_skills = list_skill_dirs(global_root, source="global", include_content=include_content)
    by_name = {skill.name: skill for skill in global_skills}
    by_name.update({skill.name: skill for skill in local})
    return tuple(by_name[name] for name in sorted(by_name))


def actor_skills_view(
    *,
    global_root: Path,
    actor_workspace: Path,
    scope: SkillScope,
) -> ActorSkillsView:
    return ActorSkillsView(
        global_skills=list_skill_dirs(
            global_root,
            source="global",
            include_content=False,
        ),
        local_skills=list_skill_dirs(
            local_skills_dir(actor_workspace),
            source="local",
            include_content=False,
        ),
        loaded_skills=loaded_skills(
            global_root=global_root,
            actor_workspace=actor_workspace,
            scope=scope,
            include_content=False,
        ),
    )


def import_global_skill(
    *,
    global_root: Path,
    actor_workspace: Path,
    skill_name: str,
) -> SkillInfo:
    source = _skill_dir(global_root, skill_name)
    if source is None:
        raise LookupError(f"global skill {skill_name!r} does not exist")
    target_root = local_skills_dir(actor_workspace)
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / skill_name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, symlinks=True)
    return SkillInfo(
        name=skill_name,
        source="local",
        path=str(target),
        description=_load_skill_metadata(
            target / "SKILL.md",
            include_body=False,
        ).description,
    )


def delete_local_skill(
    *,
    actor_workspace: Path,
    skill_name: str,
) -> bool:
    target = _skill_dir(local_skills_dir(actor_workspace), skill_name)
    if target is None:
        return False
    shutil.rmtree(target)
    return True


def _skill_dir(root: Path, skill_name: str) -> Path | None:
    if not skill_name or "/" in skill_name or "\\" in skill_name:
        raise ValueError("skill name must be a single directory name")
    path = root / skill_name
    if not (path / "SKILL.md").is_file():
        return None
    return path


class _SkillMetadata(msgspec.Struct, frozen=True):
    description: str
    body: str


def _load_skill_metadata(skill_md: Path, *, include_body: bool) -> _SkillMetadata:
    stat = skill_md.stat()
    key = (
        str(skill_md.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        include_body,
    )
    with _SKILL_METADATA_CACHE_LOCK:
        cached = _SKILL_METADATA_CACHE.get(key)
        if cached is not None:
            _SKILL_METADATA_CACHE.move_to_end(key)
            return cached
    raw_content = (
        skill_md.read_text(encoding="utf-8")
        if include_body
        else _read_skill_frontmatter(skill_md)
    )
    metadata = _parse_skill_metadata(raw_content)
    with _SKILL_METADATA_CACHE_LOCK:
        _SKILL_METADATA_CACHE[key] = metadata
        _SKILL_METADATA_CACHE.move_to_end(key)
        while len(_SKILL_METADATA_CACHE) > _SKILL_METADATA_CACHE_CAPACITY:
            _SKILL_METADATA_CACHE.popitem(last=False)
    return metadata


def _read_skill_frontmatter(skill_md: Path) -> str:
    with skill_md.open(encoding="utf-8") as handle:
        first_line = handle.readline()
        if first_line.rstrip("\r\n") != "---":
            return ""
        lines = [first_line]
        for line in handle:
            lines.append(line)
            if line.rstrip("\r\n") == "---":
                break
        return "".join(lines)


def _parse_skill_metadata(raw_content: str) -> _SkillMetadata:
    frontmatter, body = _split_frontmatter(raw_content)
    metadata = yaml.safe_load(frontmatter) if frontmatter else {}
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("SKILL.md frontmatter must be a mapping")
    raw_description = metadata.get("description", "")
    if raw_description is None:
        raw_description = ""
    if not isinstance(raw_description, str):
        raise ValueError("SKILL.md description must be a string")
    return _SkillMetadata(
        description=raw_description.strip(),
        body=body.strip(),
    )


def _split_frontmatter(raw_content: str) -> tuple[str, str]:
    if not raw_content.startswith(("---\n", "---\r\n")):
        return "", raw_content
    frontmatter_start = raw_content.find("\n") + 1
    end = raw_content.find("\n---", frontmatter_start)
    if end == -1:
        return "", raw_content
    body_start = end + len("\n---")
    if raw_content[body_start : body_start + 2] == "\r\n":
        body_start += 2
    elif raw_content[body_start : body_start + 1] == "\n":
        body_start += 1
    return raw_content[frontmatter_start:end], raw_content[body_start:]
