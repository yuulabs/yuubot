from __future__ import annotations

from pathlib import Path

from yuubot.web.files import directory_snapshot


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
