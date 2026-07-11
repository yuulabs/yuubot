from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from yuubot.web.files import directory_snapshot, workspace_media_type, workspace_zip


def test_directory_snapshot_sorts_directories_before_files(tmp_path: Path) -> None:
    (tmp_path / "zeta.txt").write_text("z", encoding="utf-8")
    (tmp_path / "beta").mkdir()
    (tmp_path / "alpha.txt").write_text("a", encoding="utf-8")
    (tmp_path / "alpha").mkdir()

    snapshot = directory_snapshot(tmp_path, tmp_path)

    assert [(entry.kind, entry.name) for entry in snapshot.entries] == [
        ("directory", "alpha"),
        ("directory", "beta"),
        ("file", "alpha.txt"),
        ("file", "zeta.txt"),
    ]


def test_workspace_media_type_recognizes_plain_text_files(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1", encoding="utf-8")
    readme = tmp_path / "README"
    readme.write_text("hello", encoding="utf-8")
    binary = tmp_path / "payload"
    binary.write_bytes(b"\x00\xff")

    assert workspace_media_type(lock) == "text/plain"
    assert workspace_media_type(readme) == "text/plain"
    assert workspace_media_type(binary) == "application/octet-stream"


def test_workspace_zip_preserves_relative_paths_and_directories(tmp_path: Path) -> None:
    (tmp_path / "reports" / "daily").mkdir(parents=True)
    (tmp_path / "reports" / "daily" / "note.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "other.txt").write_text("other", encoding="utf-8")

    with ZipFile(BytesIO(workspace_zip(tmp_path, ["reports", "other.txt"]))) as archive:
        assert sorted(archive.namelist()) == ["other.txt", "reports/daily/note.txt"]
        assert archive.read("reports/daily/note.txt") == b"hello"


@pytest.mark.parametrize("paths", [[], ["missing"], ["../outside"]])
def test_workspace_zip_rejects_invalid_selection(tmp_path: Path, paths: list[str]) -> None:
    with pytest.raises((ValueError, FileNotFoundError)):
        workspace_zip(tmp_path, paths)
