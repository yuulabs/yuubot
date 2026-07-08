from pathlib import Path
from urllib.parse import unquote


def safe_workspace_path(
    root: Path,
    rel: str,
    url_decode: bool = False,
    allow_absolute: bool = False,
    escape_error: type[Exception] = ValueError,
) -> Path:
    raw = unquote(rel).lstrip("/") if url_decode else rel
    if allow_absolute:
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
    else:
        path = root / raw
    resolved = path.resolve()
    resolved_root = root.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise escape_error(f"path escapes workspace: {rel}")
    return resolved


def ensure_contained(root: Path, path: Path, escape_error: type[Exception] = ValueError) -> None:
    resolved = path.resolve()
    resolved_root = root.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise escape_error(f"path escapes workspace: {path}")
