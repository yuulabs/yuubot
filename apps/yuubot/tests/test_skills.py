from __future__ import annotations

from pathlib import Path

import pytest

import yuubot.core.skills as skills_module
from yuubot.core.skills import list_skill_dirs


def test_skill_metadata_cache_reuses_unchanged_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated skill discovery should not reread an unchanged SKILL.md."""
    skills_module._SKILL_METADATA_CACHE.clear()
    _write_skill(tmp_path, "cached", "cached skill", "BODY_SHOULD_NOT_LOAD")
    opens = 0
    original_open = Path.open

    def counting_open(self: Path, *args: object, **kwargs: object) -> object:
        nonlocal opens
        if self.name == "SKILL.md":
            opens += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)

    first = list_skill_dirs(tmp_path, source="global", include_content=False)
    second = list_skill_dirs(tmp_path, source="global", include_content=False)

    assert first == second
    assert first[0].description == "cached skill"
    assert first[0].content == ""
    assert opens == 1


def test_skill_metadata_cache_invalidates_when_file_changes(tmp_path: Path) -> None:
    """The cache key includes file stat data so edited skills refresh."""
    skills_module._SKILL_METADATA_CACHE.clear()
    skill_md = _write_skill(tmp_path, "changed", "old description", "old body")

    assert (
        list_skill_dirs(tmp_path, source="global", include_content=False)[0].description
        == "old description"
    )

    skill_md.write_text(
        "---\nname: changed\ndescription: new description\n---\n\nnew body with more bytes",
        encoding="utf-8",
    )

    assert (
        list_skill_dirs(tmp_path, source="global", include_content=False)[0].description
        == "new description"
    )


def test_skill_metadata_cache_enforces_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded cache size prevents unbounded memory growth across workspaces."""
    skills_module._SKILL_METADATA_CACHE.clear()
    monkeypatch.setattr(skills_module, "_SKILL_METADATA_CACHE_CAPACITY", 2)

    for index in range(3):
        list_skill_dirs(
            _skill_root(tmp_path, index),
            source="global",
            include_content=False,
        )

    assert len(skills_module._SKILL_METADATA_CACHE) == 2
    cached_paths = {key[0] for key in skills_module._SKILL_METADATA_CACHE}
    assert str((_skill_root(tmp_path, 0) / "skill" / "SKILL.md").resolve()) not in cached_paths


def _skill_root(tmp_path: Path, index: int) -> Path:
    root = tmp_path / f"root-{index}"
    _write_skill(root, "skill", f"description {index}", f"body {index}")
    return root


def _write_skill(root: Path, name: str, description: str, body: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )
    return skill_md
