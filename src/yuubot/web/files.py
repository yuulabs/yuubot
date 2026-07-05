import mimetypes
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from fastapi import UploadFile

from ..app import Yuubot


def actor_workspace(app: Yuubot, actor_id: str) -> Path | None:
    return app._actor_workspace_path(actor_id)


def workspace_path(workspace: Path, value: str) -> Path:
    raw = unquote(value).lstrip("/")
    candidate = (workspace / raw).resolve()
    if candidate != workspace and workspace not in candidate.parents:
        raise ValueError(f"path escapes workspace: {value}")
    return candidate


async def save_uploads(workspace: Path, uploads: list[UploadFile], destination: str | None = None) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    target_dir = workspace_path(workspace, destination) if destination is not None else None
    if target_dir is not None and not target_dir.is_dir():
        raise ValueError("upload destination is not a directory")
    for upload in uploads:
        filename = upload.filename
        if not filename:
            continue
        safe_name = safe_filename(filename)
        data = await upload.read()
        mime = upload.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        relative = unique_upload_path(workspace, mime, safe_name, destination=destination)
        target = workspace_path(workspace, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append(
            {
                "kind": "file",
                "path": relative,
                "mime": mime,
                "meta": {"name": safe_name, "size": len(data)},
            }
        )
    if not files:
        raise ValueError("multipart body contains no files")
    return files


def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    if not name or name in {".", ".."}:
        raise ValueError("uploaded file name is required")
    return name


def safe_entry_name(name: str) -> str:
    stripped = name.strip()
    if not stripped or stripped in {".", ".."} or Path(stripped).name != stripped:
        raise ValueError("entry name is invalid")
    return stripped


def unique_upload_path(workspace: Path, mime: str, filename: str, destination: str | None = None) -> str:
    directory = _normalize_upload_destination(destination) if destination is not None else "uploads/" + mime_dir(mime)
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = _join_rel(directory, filename)
    index = 1
    while workspace_path(workspace, candidate).exists():
        candidate = _join_rel(directory, f"{stem}-{index}{suffix}")
        index += 1
    return candidate


def mime_dir(mime: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in mime).strip("-") or "application-octet-stream"


def directory_snapshot(workspace: Path, target: Path) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for child in sorted(target.iterdir(), key=lambda item: item.name):
        resolved = child.resolve()
        if resolved != workspace and workspace not in resolved.parents:
            continue
        stat = child.stat()
        relative = child.relative_to(workspace).as_posix()
        entry: dict[str, object] = {
            "name": child.name,
            "path": relative,
            "kind": "directory" if child.is_dir() else "file",
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        }
        if child.is_file():
            entry["mime"] = mimetypes.guess_type(child.name)[0] or "application/octet-stream"
        entries.append(entry)
    return {"path": target.relative_to(workspace).as_posix() if target != workspace else "", "entries": entries}


def make_directory(workspace: Path, path: str) -> dict[str, object]:
    target = workspace_path(workspace, path)
    if target == workspace:
        raise ValueError("directory path is required")
    if target.exists():
        raise FileExistsError(f"entry already exists: {path}")
    parent = target.parent
    if not parent.is_dir():
        raise FileNotFoundError(f"parent directory not found: {parent.relative_to(workspace).as_posix()}")
    target.mkdir()
    return directory_snapshot(workspace, parent)


def rename_entry(workspace: Path, path: str, name: str) -> dict[str, object]:
    source = workspace_path(workspace, path)
    if source == workspace:
        raise ValueError("cannot rename workspace root")
    if not source.exists():
        raise FileNotFoundError(f"entry not found: {path}")
    target = source.parent / safe_entry_name(name)
    _ensure_contained(workspace, target)
    if target.exists() and target != source:
        raise FileExistsError(f"entry already exists: {target.relative_to(workspace).as_posix()}")
    source.rename(target)
    return directory_snapshot(workspace, target.parent)


def delete_entries(workspace: Path, paths: list[str]) -> dict[str, object]:
    if not paths:
        raise ValueError("paths are required")
    targets = [workspace_path(workspace, path) for path in paths]
    for target in targets:
        if target == workspace:
            raise ValueError("cannot delete workspace root")
        if not target.exists():
            raise FileNotFoundError(f"entry not found: {target.relative_to(workspace).as_posix()}")
    parent = targets[0].parent
    for target in sorted(targets, key=lambda item: len(item.parts), reverse=True):
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    return directory_snapshot(workspace, parent if parent.exists() else workspace)


def move_entries(workspace: Path, sources: list[str], destination: str) -> dict[str, object]:
    if not sources:
        raise ValueError("sources are required")
    target_dir = workspace_path(workspace, destination)
    if not target_dir.is_dir():
        raise FileNotFoundError(f"destination directory not found: {destination}")

    moves: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for source_path in sources:
        source = workspace_path(workspace, source_path)
        if source == workspace:
            raise ValueError("cannot move workspace root")
        if not source.exists():
            raise FileNotFoundError(f"entry not found: {source_path}")
        target = target_dir / source.name
        _ensure_contained(workspace, target)
        if source == target:
            continue
        if source.is_dir() and (target_dir == source or source in target_dir.parents):
            raise ValueError("cannot move a directory into itself")
        if target.exists() or target in seen:
            raise FileExistsError(f"entry already exists: {target.relative_to(workspace).as_posix()}")
        seen.add(target)
        moves.append((source, target))

    for source, target in moves:
        source.rename(target)
    return directory_snapshot(workspace, target_dir)


def _normalize_upload_destination(destination: str) -> str:
    return unquote(destination).strip().strip("/")


def _join_rel(directory: str, filename: str) -> str:
    return f"{directory}/{filename}" if directory else filename


def _ensure_contained(workspace: Path, path: Path) -> None:
    resolved = path.resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise ValueError(f"path escapes workspace: {path}")
