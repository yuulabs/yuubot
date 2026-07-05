from pathlib import Path

from ..util.paths import safe_workspace_path


def workspace(path: Path) -> Path:
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace_path(workspace: Path, value: str) -> Path:
    return safe_workspace_path(workspace, value)
