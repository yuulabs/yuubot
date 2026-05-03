"""File browser API for the admin panel."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/files")

_DEFAULT_ROOT = Path(os.environ.get("YUU_WORKSPACE_ROOT", "/workspace"))


def _resolve(path: str) -> Path:
    if path.startswith("/"):
        return Path(path).resolve()
    return (_DEFAULT_ROOT / path).resolve()


@router.get("/list")
async def list_dir(path: str = Query(default="")) -> JSONResponse:
    target = _resolve(path) if path else _DEFAULT_ROOT
    if not target.exists():
        target = Path("/") if target == _DEFAULT_ROOT else None
        if target is None:
            raise HTTPException(404, "Not found")
    if not target.is_dir():
        raise HTTPException(400, "Not a directory")
    try:
        entries = []
        for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "type": "file" if child.is_file() else "dir",
                    "size": stat.st_size if child.is_file() else None,
                    "mtime": int(stat.st_mtime),
                })
            except OSError:
                pass
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc

    parent = str(target.parent) if str(target) != "/" else None
    return JSONResponse({"path": str(target), "parent": parent, "entries": entries})


@router.post("/upload")
async def upload_file(
    path: str = Query(default=""),
    file: UploadFile = File(...),
) -> JSONResponse:
    target_dir = _resolve(path) if path else _DEFAULT_ROOT
    if not target_dir.exists():
        raise HTTPException(404, "Target directory not found")
    if not target_dir.is_dir():
        raise HTTPException(400, "Target must be a directory")

    filename = Path(file.filename or "upload").name
    dest = target_dir / filename
    try:
        content = await file.read()
        dest.write_bytes(content)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc

    return JSONResponse({"saved": str(dest), "size": len(content)})
