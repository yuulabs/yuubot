"""Round-trip tests for the data-directory export/import archive format."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from yuubot.runtime.archive import (
    ArchiveError,
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    export_data,
    import_data,
)


def _seed_data_dir(root: Path) -> None:
    (root / "yuubot").mkdir(parents=True)
    (root / "yuubot" / "yuubot.db").write_bytes(b"sqlite-bytes")
    (root / "yuubot" / "logs").mkdir()
    (root / "yuubot" / "logs" / "daemon.log").write_text("line\n")
    integrations = root / "integrations" / "echo-1"
    integrations.mkdir(parents=True)
    (integrations / "messages.sqlite").write_bytes(b"messages")
    (root / "workspace").mkdir()
    (root / "skills").mkdir()


def test_export_writes_manifest_and_data_payload(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _seed_data_dir(data_dir)

    out = export_data(data_dir, tmp_path / "snap.zip")
    assert out.is_file()

    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
        assert MANIFEST_FILENAME in names
        assert "data/yuubot/yuubot.db" in names
        assert "data/integrations/echo-1/messages.sqlite" in names
        manifest = json.loads(archive.read(MANIFEST_FILENAME))
        assert manifest["manifest_version"] == MANIFEST_VERSION
        assert manifest["data_root"] == "data"
        assert "core" in manifest["categories"]


def test_import_round_trip_restores_files(tmp_path: Path) -> None:
    source = tmp_path / "src"
    _seed_data_dir(source)
    archive = export_data(source, tmp_path / "out.zip")

    target = tmp_path / "restored"
    manifest = import_data(archive, target)

    assert manifest.manifest_version == MANIFEST_VERSION
    assert (target / "yuubot" / "yuubot.db").read_bytes() == b"sqlite-bytes"
    assert (
        target / "integrations" / "echo-1" / "messages.sqlite"
    ).read_bytes() == b"messages"
    assert (target / "yuubot" / "logs" / "daemon.log").read_text() == "line\n"


def test_import_replace_wipes_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "src"
    _seed_data_dir(source)
    archive = export_data(source, tmp_path / "out.zip")

    target = tmp_path / "restored"
    target.mkdir()
    (target / "stale.txt").write_text("old")

    import_data(archive, target, replace=True)

    assert not (target / "stale.txt").exists()
    assert (target / "yuubot" / "yuubot.db").is_file()


def test_import_rejects_archive_without_manifest(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("data/x.txt", "hello")

    with pytest.raises(ArchiveError, match="manifest"):
        import_data(bad, tmp_path / "target")


def test_import_rejects_path_traversal(tmp_path: Path) -> None:
    bad = tmp_path / "evil.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr(
            MANIFEST_FILENAME,
            json.dumps(
                {
                    "manifest_version": MANIFEST_VERSION,
                    "created_at": "now",
                    "yuubot_version": "test",
                    "categories": ["core"],
                    "data_root": "data",
                }
            ),
        )
        zf.writestr("data/../escape.txt", "no")

    with pytest.raises(ArchiveError, match="outside"):
        import_data(bad, tmp_path / "target")
