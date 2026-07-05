from pathlib import Path


def workspace(path: Path) -> Path:
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace_path(workspace: Path, value: str) -> Path:
    path = (workspace / value).resolve()
    if path != workspace and workspace not in path.parents:
        raise ValueError(f"path escapes workspace: {value}")
    return path
