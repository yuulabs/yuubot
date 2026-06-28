"""Conversation upload path helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .bindings import ConversationUploadBinding, ConversationUploadedFile

if TYPE_CHECKING:
    from .manager import ConversationManager


def _conversation_upload_slug(conversation_id: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", conversation_id).strip("._-")
    stem = stem[:24] or "conversation"
    digest = hashlib.sha1(conversation_id.encode()).hexdigest()[:8]
    return f"{stem}-{digest}"


def _safe_upload_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    safe = re.sub(r"[^A-Za-z0-9_. -]", "_", name).strip(" ._-")
    if not safe:
        safe = "upload"
    if len(safe) > 160:
        suffix = "".join(Path(safe).suffixes)
        stem_limit = max(1, 160 - len(suffix))
        safe = f"{Path(safe).stem[:stem_limit]}{suffix}"
    return safe


def _unique_upload_path(upload_dir: Path, filename: str) -> Path:
    root = upload_dir.resolve()
    target = (upload_dir / filename).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"filename {filename!r} escapes upload directory")
    if not target.exists():
        return target

    stem = Path(filename).stem or "upload"
    suffix = Path(filename).suffix
    for index in range(1, 1000):
        candidate = (upload_dir / f"{stem}-{index}{suffix}").resolve()
        if not candidate.exists():
            return candidate
    raise ValueError(f"too many uploaded files named {filename!r}")


def _workspace_url_segment(relative_name: str) -> str:
    segment = relative_name.strip().strip("/")
    if not segment:
        raise ValueError("workspace path must not be empty")
    return "/".join(part for part in segment.split("/") if part)


async def store_uploads(
    manager: ConversationManager,
    *,
    conversation_id: str,
    files: list[tuple[str, bytes, str]],
    binding: ConversationUploadBinding | None = None,
) -> list[ConversationUploadedFile]:
    if not files:
        raise ValueError("at least one file must be provided")

    workspace_path, workspace_url_segment = await _upload_workspace(
        manager,
        conversation_id=conversation_id,
        binding=binding,
    )
    upload_dir = (
        workspace_path / "uploads" / _conversation_upload_slug(conversation_id)
    ).resolve()
    if not upload_dir.is_relative_to(workspace_path.resolve()):
        raise ValueError("upload path escapes workspace")
    upload_dir.mkdir(parents=True, exist_ok=True)

    uploaded: list[ConversationUploadedFile] = []
    for filename, content, content_type in files:
        safe_name = _safe_upload_filename(filename)
        target = _unique_upload_path(upload_dir, safe_name)
        target.write_bytes(content)
        relative_path = target.relative_to(workspace_path.resolve()).as_posix()
        uploaded.append(
            ConversationUploadedFile(
                name=safe_name,
                path=relative_path,
                url=f"/workspace/{workspace_url_segment}/{relative_path}",
                size=len(content),
                content_type=content_type,
            )
        )
    return uploaded


async def _upload_workspace(
    manager: ConversationManager,
    *,
    conversation_id: str,
    binding: ConversationUploadBinding | None,
) -> tuple[Path, str]:
    conversation = await manager.store.get_conversation(conversation_id)
    if conversation is not None:
        actor_id = conversation.actor_id
    else:
        actor_id = (binding.actor_id if binding is not None else "").strip()
        if not actor_id:
            raise LookupError(
                f"upload for new conversation {conversation_id!r} requires actor_id"
            )

    actor = await manager._active_actor(actor_id)
    capability_set = await manager._require_capability_set(actor.capability_set_id)
    workspace_path = manager._resolve_workspace_path(capability_set.workspace_path)
    if workspace_path is None:
        raise ValueError(f"actor {actor.id!r} has no configured workspace")
    return workspace_path, _workspace_url_segment(capability_set.workspace_path)
