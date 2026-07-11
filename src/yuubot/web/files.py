import mimetypes
import hashlib
import os
import shutil
import tempfile
import zipfile
from io import BytesIO
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

import msgspec
from fastapi import UploadFile

from ..app import Yuubot
from ..util.paths import ensure_contained, safe_workspace_path


class UploadFileMeta(msgspec.Struct, frozen=True):
    name: str
    size: int


class UploadFileInfo(msgspec.Struct, frozen=True, kw_only=True):
    kind: str = "file"
    path: str
    mime: str
    meta: UploadFileMeta


class DirectoryEntry(msgspec.Struct, frozen=True):
    name: str
    path: str
    kind: str
    size: int
    mtime: str
    mime: str = ""


class DirectorySnapshot(msgspec.Struct, frozen=True):
    path: str
    entries: list[DirectoryEntry]


_PLAIN_TEXT_SUFFIXES = {
    ".cfg", ".conf", ".css", ".env", ".ini", ".js", ".json", ".lock", ".log",
    ".md", ".py", ".sh", ".toml", ".ts", ".tsx", ".yaml", ".yml",
}

MAX_EDITABLE_FILE_BYTES = 10 * 1024 * 1024


def workspace_media_type(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed:
        return guessed
    if path.suffix.lower() in _PLAIN_TEXT_SUFFIXES or _is_utf8_text(path):
        return "text/plain"
    return "application/octet-stream"


def workspace_file_etag(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(128 * 1024), b""):
            digest.update(chunk)
    return f'"{digest.hexdigest()}"'


def replace_workspace_text(path: Path, content: bytes, expected_etag: str | None) -> str:
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("file not found")
    if len(content) > MAX_EDITABLE_FILE_BYTES:
        raise ValueError("file is too large to edit")
    try:
        content.decode("utf-8")
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("only UTF-8 text files can be edited") from exc
    current_etag = workspace_file_etag(path)
    if expected_etag is None:
        raise PermissionError("If-Match header is required")
    if expected_etag != current_etag:
        raise FileExistsError("file changed since it was loaded")

    mode = path.stat().st_mode
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return workspace_file_etag(path)


def workspace_zip(workspace: Path, paths: list[str]) -> bytes:
    if not paths:
        raise ValueError("paths are required")
    targets = [(path, workspace_path(workspace, path)) for path in paths]
    for path, target in targets:
        if target == workspace or not target.exists():
            raise FileNotFoundError(f"entry not found: {path}")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        added: set[str] = set()
        for _, target in targets:
            files = target.rglob("*") if target.is_dir() else (target,)
            if target.is_dir() and not any(target.iterdir()):
                name = target.relative_to(workspace).as_posix().rstrip("/") + "/"
                archive.writestr(name, b"")
            for child in files:
                if not child.is_file():
                    continue
                _ensure_contained(workspace, child.resolve())
                name = child.relative_to(workspace).as_posix()
                if name not in added:
                    archive.write(child, name)
                    added.add(name)
    return output.getvalue()


def _is_utf8_text(path: Path) -> bool:
    try:
        sample = path.open("rb").read(8192)
        sample.decode("utf-8")
        return b"\x00" not in sample
    except (OSError, UnicodeDecodeError):
        return False


def actor_workspace(app: Yuubot, actor_id: str) -> Path | None:
    return app.actor_workspace_path(actor_id)


def workspace_path(workspace: Path, value: str) -> Path:
    return safe_workspace_path(workspace, value, True)


def editable_workspace_path(workspace: Path, value: str) -> Path:
    raw = unquote(value).lstrip("/")
    candidate = workspace
    for part in Path(raw).parts:
        candidate /= part
        if candidate.is_symlink():
            raise ValueError("symbolic links cannot be edited")
    return workspace_path(workspace, value)


async def save_uploads(workspace: Path, uploads: list[UploadFile], destination: str | None = None) -> list[UploadFileInfo]:
    files: list[UploadFileInfo] = []
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
        relative = unique_upload_path(workspace, mime, safe_name, destination)
        target = workspace_path(workspace, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append(
            UploadFileInfo(
                path=relative,
                mime=mime,
                meta=UploadFileMeta(safe_name, len(data)),
            )
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


def directory_snapshot(workspace: Path, target: Path) -> DirectorySnapshot:
    entries: list[DirectoryEntry] = []
    for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold(), item.name)):
        resolved = child.resolve()
        if resolved != workspace and workspace not in resolved.parents:
            continue
        stat = child.stat()
        relative = child.relative_to(workspace).as_posix()
        mime = ""
        if child.is_file():
            mime = workspace_media_type(child)
        entries.append(
            DirectoryEntry(
                child.name,
                relative,
                "directory" if child.is_dir() else "file",
                stat.st_size,
                datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                mime,
            )
        )
    return DirectorySnapshot(
        target.relative_to(workspace).as_posix() if target != workspace else "",
        entries,
    )


def make_directory(workspace: Path, path: str) -> DirectorySnapshot:
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


def rename_entry(workspace: Path, path: str, name: str) -> DirectorySnapshot:
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


def delete_entries(workspace: Path, paths: list[str]) -> DirectorySnapshot:
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


def move_entries(workspace: Path, sources: list[str], destination: str) -> DirectorySnapshot:
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
    ensure_contained(workspace, path)
