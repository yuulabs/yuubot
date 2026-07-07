"""Git-based update checks."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


async def _run_git(root: Path, *args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return int(process.returncode or 0), stdout.decode("utf-8", errors="replace").strip(), stderr.decode("utf-8", errors="replace").strip()


async def git_commit(root: Path, rev: str) -> str | None:
    code, stdout, _stderr = await _run_git(root, "rev-parse", rev)
    if code != 0 or not stdout:
        return None
    return stdout


async def check_git_update(root: Path) -> tuple[str | None, str | None, bool, str]:
    if shutil.which("git") is None:
        return None, None, False, "git was not found on PATH"

    fetch_code, _fetch_out, fetch_err = await _run_git(root, "fetch", "origin")
    if fetch_code != 0:
        return None, None, False, fetch_err or "git fetch origin failed"

    current = await git_commit(root, "HEAD")
    if current is None:
        return None, None, False, "could not resolve current commit"

    upstream_code, upstream, _upstream_err = await _run_git(root, "rev-parse", "@{u}")
    if upstream_code != 0 or not upstream:
        return current, None, False, "no upstream tracking branch configured"

    remote = await git_commit(root, upstream)
    if remote is None:
        return current, None, False, "could not resolve upstream commit"

    ahead_code, ahead_out, _ahead_err = await _run_git(root, "rev-list", "--count", f"HEAD..{upstream}")
    if ahead_code != 0:
        return current, remote, False, "could not compare local and upstream commits"

    try:
        ahead_count = int(ahead_out or "0")
    except ValueError:
        return current, remote, False, "invalid rev-list output"

    return current, remote, ahead_count > 0, ""
