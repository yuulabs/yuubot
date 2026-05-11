"""Actor workspace path allocation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ActorWorkspaceResolver:
    """Allocates stable per-actor workspace directories under one root."""

    root: Path

    def resolve(self, actor_id: str) -> Path:
        if not actor_id or not actor_id.strip():
            raise ValueError("actor_id must not be empty")

        workspace_root = self.root.expanduser().resolve()
        actors_root = workspace_root / "actors"
        workspace_path = (actors_root / safe_actor_path_id(actor_id)).resolve()
        if not workspace_path.is_relative_to(actors_root):
            raise ValueError(f"actor_id {actor_id!r} escapes workspace root")

        workspace_path.mkdir(parents=True, exist_ok=True)
        return workspace_path


def safe_actor_path_id(actor_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", actor_id).strip("._-") or "actor"
    while ".." in slug:
        slug = slug.replace("..", "_")
    digest = hashlib.sha1(actor_id.encode()).hexdigest()[:8]
    return f"{slug}-{digest}"
