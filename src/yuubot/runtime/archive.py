"""Zip-based export/import of the yuubot data directory.

The platform stores all on-disk state under a single ``data_dir``
(see ``yuubot.bootstrap.layout.DataLayout``). Exporting is a structural
operation: we zip the directory tree and write a ``manifest.json`` that
records the manifest version, timestamp, and the relative root.

The first version produces a single ``core`` category — the entire data
directory. Per-integration message archives stay inside
``<data_dir>/integrations/<id>/`` and are carried along automatically.
Splitting traces or messages into their own buckets is a future change.

Both export and import expect the daemon and admin processes to be stopped
so files are not actively written during the snapshot.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import cast

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "manifest.json"
DATA_PREFIX = "data"


class ArchiveError(ValueError):
    """Raised when an archive does not match the expected manifest layout."""


@dataclass(frozen=True)
class ArchiveManifest:
    manifest_version: int
    created_at: str
    yuubot_version: str
    categories: tuple[str, ...]
    data_root: str

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "created_at": self.created_at,
            "yuubot_version": self.yuubot_version,
            "categories": list(self.categories),
            "data_root": self.data_root,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ArchiveManifest":
        if not isinstance(raw, dict):
            raise ArchiveError("manifest must be a JSON object")
        data = cast("dict[str, object]", raw)
        try:
            manifest_version_raw = data["manifest_version"]
            created_at_raw = data["created_at"]
            if not isinstance(manifest_version_raw, int):
                raise TypeError("manifest_version must be an integer")
            if not isinstance(created_at_raw, str):
                raise TypeError("created_at must be a string")
            manifest_version = manifest_version_raw
            created_at = created_at_raw
            yuubot_version = str(data.get("yuubot_version", ""))
            categories_raw = data.get("categories", ["core"])
            if not isinstance(categories_raw, list):
                raise TypeError("categories must be a list")
            categories = tuple(str(c) for c in cast("list[object]", categories_raw))
            data_root = str(data.get("data_root", DATA_PREFIX))
        except (KeyError, TypeError, ValueError) as exc:
            raise ArchiveError(f"manifest missing required field: {exc}") from None
        if manifest_version != MANIFEST_VERSION:
            raise ArchiveError(f"unsupported manifest version {manifest_version}")
        return cls(
            manifest_version=manifest_version,
            created_at=created_at,
            yuubot_version=yuubot_version,
            categories=categories,
            data_root=data_root,
        )


def export_data(data_dir: Path | str, out_zip: Path | str) -> Path:
    """Zip ``data_dir`` to ``out_zip`` with a manifest at the archive root.

    The archive contains:
        manifest.json
        data/...   (mirror of data_dir contents)
    """
    source = Path(data_dir).expanduser().resolve()
    if not source.is_dir():
        raise ArchiveError(f"data_dir {source} is not a directory")
    out_path = Path(out_zip).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = ArchiveManifest(
        manifest_version=MANIFEST_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        yuubot_version=_yuubot_version(),
        categories=("core",),
        data_root=DATA_PREFIX,
    )

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_FILENAME, json.dumps(manifest.to_dict(), indent=2))
        for entry in _iter_files(source):
            relative = entry.relative_to(source)
            archive.write(entry, arcname=f"{DATA_PREFIX}/{relative.as_posix()}")
    return out_path


def import_data(
    in_zip: Path | str,
    data_dir: Path | str,
    *,
    replace: bool = False,
) -> ArchiveManifest:
    """Extract an archive into ``data_dir``.

    When ``replace`` is False (default) the archive contents are unioned
    onto the existing directory; when True, the destination is wiped first.
    Returns the parsed manifest so the caller can log/audit it.
    """
    archive_path = Path(in_zip).expanduser().resolve()
    target = Path(data_dir).expanduser().resolve()

    if not archive_path.is_file():
        raise ArchiveError(f"archive {archive_path} does not exist")
    if not zipfile.is_zipfile(archive_path):
        raise ArchiveError(f"{archive_path} is not a zip archive")

    with zipfile.ZipFile(archive_path) as archive:
        manifest = _read_manifest(archive)
        prefix = manifest.data_root.rstrip("/")
        if not prefix:
            raise ArchiveError("manifest data_root must not be empty")

        if replace and target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        for member in archive.infolist():
            if member.filename == MANIFEST_FILENAME:
                continue
            if not member.filename.startswith(f"{prefix}/"):
                continue
            relative = member.filename[len(prefix) + 1 :]
            if not relative or relative.endswith("/"):
                continue
            destination = (target / relative).resolve()
            if not destination.is_relative_to(target):
                raise ArchiveError(
                    f"refusing to extract {member.filename} outside data_dir"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)

    return manifest


def _iter_files(root: Path):
    for entry in sorted(root.rglob("*")):
        if entry.is_file():
            yield entry


def _read_manifest(archive: zipfile.ZipFile) -> ArchiveManifest:
    try:
        raw = archive.read(MANIFEST_FILENAME)
    except KeyError:
        raise ArchiveError("archive is missing manifest.json") from None
    try:
        payload = json.loads(raw.decode())
    except json.JSONDecodeError as exc:
        raise ArchiveError(f"invalid manifest JSON: {exc}") from None
    return ArchiveManifest.from_dict(payload)


def _yuubot_version() -> str:
    try:
        return metadata.version("yuubot")
    except metadata.PackageNotFoundError:
        return ""
