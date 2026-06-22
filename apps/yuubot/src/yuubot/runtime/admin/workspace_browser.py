"""Admin workspace browser — direct path-based serving.

Serves ``<data_dir>/workspace`` over HTTP exactly like ``python -m http.server``
rooted at that directory. The user-configured ``CapabilitySet.workspace_path``
(a relative path like ``this-path``) IS the URL segment:

    GET /workspace/<workspace_path>                -> directory listing
    GET /workspace/<workspace_path>/outputs/x.html -> file response (mime guessed)

A path-escape guard is applied on every sub-path request: both ``workspace_root``
and ``target`` are resolved with ``Path.resolve()`` and the target must be
relative to the resolved root. Violations return HTTP 403. No string-based
comparison. The browser never participates in actor_id -> workspace mapping:
that is the resolver's job in ``core.actors.workspace``.
"""

from __future__ import annotations

import html
import mimetypes
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, Response

__all__ = ["make_workspace_handler"]


def make_workspace_handler(workspace_root: Path):
    """Build a Starlette handler that serves ``workspace_root`` over HTTP.

    Registered under ``Route("/workspace/{path:path}", ...)``. Starlette's
    ``path:path`` converter accepts an empty string, so ``/workspace/`` maps
    to ``sub=""`` -> ``target=root`` -> directory listing of the root.
    """
    root = workspace_root.expanduser().resolve()

    async def handler(request: Request) -> Response:
        sub = request.path_params["path"]
        target = (root / sub).resolve()
        if not target.is_relative_to(root):
            return Response("forbidden", status_code=403)
        if target.is_dir():
            return HTMLResponse(_render_listing(root, target, sub))
        if target.is_file():
            mime, _ = mimetypes.guess_type(target.name)
            return FileResponse(target, media_type=mime or "application/octet-stream")
        return Response("not found", status_code=404)

    return handler


def _render_listing(root: Path, dir_path: Path, sub: str) -> str:
    """Render an http.server-style directory listing as HTML.

    * Hidden files (dotfiles) are listed (matches ``python -m http.server``
      defaults — Phase 4 explicitly defers any dotfile-hide toggle).
    * Directory entries get a trailing ``/`` in their href and display name.
    * A ``../`` (parent directory) link appears whenever ``sub`` is non-empty
      (i.e. the listed directory is below the served root).
    """
    title_path = f"/workspace/{sub}/" if sub else "/workspace/"
    entries = sorted(dir_path.iterdir(), key=lambda p: p.name)
    lines: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>Directory listing for {html.escape(title_path)}</title>",
        "</head>",
        "<body>",
        f"<h1>Directory listing for {html.escape(title_path)}</h1>",
        "<hr>",
        "<ul>",
    ]

    if sub:
        parent_sub = _parent_posix(sub, root, dir_path)
        parent_href = (
            f"/workspace/{parent_sub}/" if parent_sub else "/workspace/"
        )
        lines.append(f'<li><a href="{html.escape(parent_href)}">../</a></li>')

    for entry in entries:
        name = entry.name
        entry_rel = _relative_posix(entry, root)
        if entry.is_dir():
            href = f"/workspace/{entry_rel}/"
        else:
            href = f"/workspace/{entry_rel}"
        display = f"{name}/" if entry.is_dir() else name
        lines.append(
            f'<li><a href="{html.escape(href)}">{html.escape(display)}</a></li>'
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


def _parent_posix(sub: str, root: Path, dir_path: Path) -> str:
    """POSIX-style relative path of ``dir_path.parent`` against ``root``."""
    return _relative_posix(dir_path.parent, root)
