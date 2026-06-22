"""Admin-served actor-workspace browser.

Exposes http.server-style directory listings and file responses under
``/workspace/{actor_id}/...`` on the admin Starlette app. The actor workspace
root is derived from the bootstrap ``data_dir`` (``<data_dir>/workspace/actors``
— see :class:`yuubot.bootstrap.layout.DataLayout`). ``safe_actor_path_id`` is
reused from :mod:`yuubot.core.actors.workspace`; this module never participates
in the actor ID → workspace mapping decision.

A path-escape guard is applied on every request that resolves a sub-path:
both ``workspace_root`` and ``target`` are resolved with ``Path.resolve()``
and the target must be relative to the resolved root. Violations return HTTP
403. No string-based comparison.
"""

from __future__ import annotations

import html
import mimetypes
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, Response

from yuubot.core.actors.workspace import safe_actor_path_id

__all__ = [
    "make_workspace_file_handler",
    "make_workspace_index_handler",
]


def make_workspace_index_handler(data_dir: Path):
    """Handle ``GET /workspace/{actor_id}/`` — top-level directory listing."""

    async def handler(request: Request) -> Response:
        actor_id = request.path_params["actor_id"]
        workspace_root = _workspace_root(data_dir, actor_id)
        if not workspace_root.is_dir():
            return Response("workspace not found", status_code=404)
        return HTMLResponse(
            _render_listing(actor_id, workspace_root, workspace_root)
        )

    return handler


def make_workspace_file_handler(data_dir: Path):
    """Handle ``GET /workspace/{actor_id}/{path}`` — nested listing or file."""

    async def handler(request: Request) -> Response:
        actor_id = request.path_params["actor_id"]
        sub = request.path_params["path"]
        workspace_root = _workspace_root(data_dir, actor_id)
        # ``sub`` may contain percent-decoded traversal segments; resolve
        # against the resolved root and verify containment.
        target = (workspace_root / sub).resolve()
        if not target.is_relative_to(workspace_root):
            return Response("forbidden", status_code=403)
        if target.is_dir():
            return HTMLResponse(
                _render_listing(actor_id, target, workspace_root)
            )
        if not target.is_file():
            return Response("not found", status_code=404)
        mime, _ = mimetypes.guess_type(target.name)
        return FileResponse(target, media_type=mime or "application/octet-stream")

    return handler


def _workspace_root(data_dir: Path, actor_id: str) -> Path:
    """Resolve the actor workspace root, always ``.resolve()``-d.

    Mirrors :class:`yuubot.core.actors.workspace.ActorWorkspaceResolver`:
    ``<data_dir>/workspace/actors/<safe_actor_path_id(actor_id)>`` without
    touching the filesystem (the directory may or may not exist).
    """
    return (
        Path(data_dir).expanduser()
        / "workspace"
        / "actors"
        / safe_actor_path_id(actor_id)
    ).resolve()


def _render_listing(
    actor_id: str,
    dir_path: Path,
    workspace_root: Path,
) -> str:
    """Render an http.server-style directory listing as HTML.

    * Hidden files (dotfiles) are listed (matches ``python -m http.server``
      defaults — Phase 4 explicitly defers any dotfile-hide toggle).
    * Directory entries get a trailing ``/`` in their href.
    * A ``../`` (parent directory) link appears only when ``dir_path`` is
      below ``workspace_root``.
    """
    rel = _relative_posix(dir_path, workspace_root)
    header = (
        f"/workspace/{html.escape(actor_id)}/" + rel
        + ("/" if rel else "")
    )

    entries = sorted(dir_path.iterdir(), key=lambda p: p.name)
    lines: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>Directory listing for {html.escape(header)}</title>",
        "</head>",
        "<body>",
        f"<h1>Directory listing for {html.escape(header)}</h1>",
        "<hr>",
        "<ul>",
    ]

    if dir_path != workspace_root:
        parent_suffix = _relative_posix(dir_path.parent, workspace_root)
        parent_href = (
            f"/workspace/{actor_id}/" + parent_suffix
            + ("/" if parent_suffix else "")
        )
        lines.append(f'<li><a href="{html.escape(parent_href)}">../</a></li>')

    for entry in entries:
        name = entry.name
        entry_suffix = _relative_posix(entry, workspace_root)
        if entry.is_dir():
            entry_suffix += "/"
        href = f"/workspace/{actor_id}/{entry_suffix}"
        lines.append(
            f'<li><a href="{html.escape(href)}">{html.escape(name)}'
            f"{'/' if entry.is_dir() else ''}</a></li>"
        )

    lines.extend(["</ul>", "<hr>", "</body>", "</html>"])
    return "\n".join(lines)


def _relative_posix(path: Path, root: Path) -> str:
    """POSIX-style relative path of *path* against *root* (no leading ./)."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return ""
    rel_posix = rel.as_posix()
    if rel_posix == ".":
        return ""
    return rel_posix
