"""Share grants: workspace directory snapshots served from published/."""

from __future__ import annotations

import logging
import mimetypes
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import quote

import msgspec
from attrs import define, field

from ..util.asyncio_ import BackgroundSweeper
from ..util.paths import safe_workspace_path
from ..util.time import utc_now_iso

if TYPE_CHECKING:
    from .store import ApplicationStateStore

_log = logging.getLogger(__name__)

EmitFn = Callable[..., None]
WorkspaceResolver = Callable[[str], Path | None]
INDEX_CANDIDATES = ("index.html", "index.htm")


class ShareGrant(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    actor_id: str
    source_path: str
    created_at: str
    expires_at: str | None
    revoked: bool = False
    kind: str = "directory"
    entry_path: str = ""


class ShareError(Exception):
    """Base error for share operations."""


class ShareNotFoundError(ShareError):
    pass


class ShareBadRequestError(ShareError):
    pass


class SharePublishError(ShareError):
    pass


def new_share_id() -> str:
    return f"sh-{uuid.uuid4().hex[:12]}"


def share_content_type(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0]
    if mime is not None:
        if mime.startswith("text/"):
            return f"{mime}; charset=utf-8"
        return mime
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        return "text/html; charset=utf-8"
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix in {".md", ".markdown", ".txt", ".log", ".csv", ".jsonl", ".toml", ".yaml", ".yml", ".py", ".ts", ".tsx", ".sh"}:
        return "text/plain; charset=utf-8"
    return "application/octet-stream"

def _parse_timestamp(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    return datetime.fromisoformat(value)


def _is_expired(expires_at: str | None, *, now: datetime | None = None) -> bool:
    if expires_at is None:
        return False
    current = now or datetime.now(UTC)
    return current >= _parse_timestamp(expires_at)


def _normalize_rel_path(rel_path: str, *, escape_error: type[ShareError] = ShareNotFoundError) -> str:
    raw = rel_path.strip().lstrip("/")
    if raw in {"", "."}:
        return ""
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if ".." in parts:
        raise escape_error("path escapes share root")
    return "/".join(parts)


def _contained_path(root: Path, rel_path: str) -> Path:
    return safe_workspace_path(root, rel_path, escape_error=ShareNotFoundError)


def _resolve_index(directory: Path) -> Path | None:
    for name in INDEX_CANDIDATES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _share_root_resolved(share_root: Path) -> Path:
    return share_root.resolve()


def _resolve_regular_file(target: Path, share_root: Path) -> Path:
    resolved = target.resolve()
    root = _share_root_resolved(share_root)
    if resolved != root and root not in resolved.parents:
        raise ShareNotFoundError("path escapes share root")
    return resolved


def _resolve_index_file(directory: Path, share_root: Path) -> Path:
    index = _resolve_index(directory)
    if index is None:
        raise ShareNotFoundError("index file not found")
    return _resolve_regular_file(index, share_root)


def _copy_entry(source: Path, destination: Path, *, source_root: Path) -> None:
    if source.is_symlink():
        try:
            resolved = source.resolve()
        except OSError:
            _log.warning("skipping broken symlink during share copy: %s", source)
            return
        if resolved != source_root and source_root not in resolved.parents:
            _log.warning("skipping symlink escaping workspace during share copy: %s -> %s", source, resolved)
            return
        _log.warning("skipping symlink during share copy: %s", source)
        return
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir(), key=lambda item: item.name):
            _copy_entry(child, destination / child.name, source_root=source_root)
        return
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_tree(source: Path, destination: Path) -> None:
    source_root = source.resolve()
    if not source_root.is_dir():
        raise ShareBadRequestError("source_path is not a directory")
    destination.mkdir(parents=True, exist_ok=True)
    for child in sorted(source_root.iterdir(), key=lambda item: item.name):
        _copy_entry(child, destination / child.name, source_root=source_root)


def _copy_source(source: Path, destination: Path) -> tuple[str, str]:
    if source.is_dir():
        _copy_tree(source, destination)
        return "directory", ""
    if source.is_file():
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / source.name
        shutil.copy2(source, target)
        return "file", source.name
    raise ShareBadRequestError("source_path is not a file or directory")


def _atomic_publish_source(source: Path, published_root: Path, share_id: str) -> tuple[Path, str, str]:
    final_dir = published_root / share_id
    tmp_dir = published_root / f"{share_id}.tmp"
    if final_dir.exists():
        raise SharePublishError(f"published directory already exists: {share_id}")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    try:
        kind, entry_path = _copy_source(source, tmp_dir)
        tmp_dir.rename(final_dir)
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        raise
    return final_dir, kind, entry_path


def _public_share_url(share_id: str, entry_path: str, *, public_url_base: str) -> str:
    base = public_url_base.rstrip("/")
    if not entry_path:
        return f"{base}/s/{share_id}/"
    encoded = "/".join(quote(part) for part in entry_path.split("/"))
    return f"{base}/s/{share_id}/{encoded}"


def share_grant_snapshot(grant: ShareGrant, *, public_url_base: str) -> dict[str, object]:
    payload = cast(dict[str, object], msgspec.to_builtins(grant))
    payload["url"] = _public_share_url(grant.id, grant.entry_path, public_url_base=public_url_base)
    return payload


@define
class ShareRegistry:
    data_dir: Path
    state: ApplicationStateStore
    emit: EmitFn
    _grants: dict[str, ShareGrant] = field(factory=dict)
    _workspace_for_actor: WorkspaceResolver | None = field(default=None)
    _sweeper: BackgroundSweeper = field(factory=BackgroundSweeper, init=False)

    @property
    def published_dir(self) -> Path:
        return self.data_dir / "published"

    def bind_workspace_resolver(self, resolver: WorkspaceResolver) -> None:
        self._workspace_for_actor = resolver

    async def load_grants(self) -> None:
        for grant in await self.state.load_share_grants():
            self._grants[grant.id] = grant

    def list_grants(self) -> list[ShareGrant]:
        return sorted(self._grants.values(), key=lambda grant: grant.id)

    def get(self, share_id: str) -> ShareGrant:
        grant = self._grants.get(share_id)
        if grant is None:
            raise ShareNotFoundError(f"share not found: {share_id}")
        return grant

    def __contains__(self, share_id: str) -> bool:
        return share_id in self._grants

    async def publish(
        self,
        *,
        actor_id: str,
        source_path: str,
        expires_at: str | None,
    ) -> ShareGrant:
        if self._workspace_for_actor is None:
            raise SharePublishError("workspace resolver is not configured")
        workspace = self._workspace_for_actor(actor_id)
        if workspace is None:
            raise ShareNotFoundError(f"actor not found: {actor_id}")
        rel = _normalize_rel_path(source_path, escape_error=ShareBadRequestError)
        source = _contained_path(workspace, rel)
        if not source.exists():
            raise ShareNotFoundError(f"source path not found: {rel}")

        share_id = new_share_id()
        self.published_dir.mkdir(parents=True, exist_ok=True)
        _, kind, entry_path = _atomic_publish_source(source, self.published_dir, share_id)

        grant = ShareGrant(
            id=share_id,
            actor_id=actor_id,
            source_path=rel,
            created_at=utc_now_iso(),
            expires_at=expires_at,
            kind=kind,
            entry_path=entry_path,
        )
        await self._persist(grant)
        self.emit(
            "share.created",
            share_id=grant.id,
            actor_id=grant.actor_id,
            source_path=grant.source_path,
        )
        return grant

    async def revoke(self, share_id: str) -> ShareGrant:
        grant = self.get(share_id)
        updated = msgspec.structs.replace(grant, revoked=True)
        self._grants.pop(share_id, None)
        await self.state.delete_share_grant(share_id)
        await self._delete_published_dir(share_id)
        self.emit("share.revoked", share_id=share_id)
        return updated

    def resolve_file(self, share_id: str, rel_path: str) -> Path:
        share_root, target = self.resolve_path(share_id, rel_path)
        if target.is_dir():
            return _resolve_index_file(target, share_root)
        if not target.is_file():
            raise ShareNotFoundError("file not found")
        return _resolve_regular_file(target, share_root)

    def resolve_path(self, share_id: str, rel_path: str) -> tuple[Path, Path]:
        grant = self.get(share_id)
        if grant.revoked or _is_expired(grant.expires_at):
            raise ShareNotFoundError("share is not available")
        share_root = self.published_dir / share_id
        if not share_root.is_dir():
            raise ShareNotFoundError("share snapshot is missing")

        normalized = _normalize_rel_path(rel_path)
        if not normalized and grant.kind == "file":
            return share_root.resolve(), _resolve_regular_file(share_root / grant.entry_path, share_root)
        target = share_root.resolve() if not normalized else _contained_path(share_root, normalized)
        return share_root.resolve(), target

    async def sweep_expired(self) -> None:
        now = datetime.now(UTC)
        for grant in list(self._grants.values()):
            if grant.revoked or not _is_expired(grant.expires_at, now=now):
                continue
            self._grants.pop(grant.id, None)
            await self.state.delete_share_grant(grant.id)
            await self._delete_published_dir(grant.id)
            self.emit("share.expired", share_id=grant.id)

    async def start_background_cleanup(self, interval_s: float = 300) -> None:
        await self._sweeper.start(interval_s, self.sweep_expired)

    async def stop_background_cleanup(self) -> None:
        await self._sweeper.stop()

    async def _persist(self, grant: ShareGrant) -> None:
        await self.state.put_share_grant(grant)
        self._grants[grant.id] = grant

    async def _delete_published_dir(self, share_id: str) -> None:
        target = self.published_dir / share_id
        if target.exists():
            shutil.rmtree(target)
        tmp = self.published_dir / f"{share_id}.tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
