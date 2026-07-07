"""Install layout detection for upgrade."""

from __future__ import annotations

import shutil
from pathlib import Path

INSTALL_KIND_GIT = "git_source"
INSTALL_KIND_PACKAGE = "package"


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def install_deps_script(root: Path | None = None) -> Path:
    resolved = root or project_root()
    return resolved / "scripts" / "install-deps.sh"


def deps_manifest(root: Path | None = None) -> Path:
    resolved = root or project_root()
    return resolved / "scripts" / "deps.yaml"


def detect_install(root: Path | None = None) -> tuple[bool, str, str]:
    resolved = root or project_root()
    if not (resolved / ".git").is_dir():
        return False, INSTALL_KIND_PACKAGE, "installation is not a git checkout"
    if shutil.which("git") is None:
        return False, INSTALL_KIND_PACKAGE, "git was not found on PATH"
    script = install_deps_script(resolved)
    if not script.is_file():
        return False, INSTALL_KIND_PACKAGE, f"missing dependency installer: {script}"
    if not deps_manifest(resolved).is_file():
        return False, INSTALL_KIND_PACKAGE, f"missing dependency manifest: {deps_manifest(resolved)}"
    return True, INSTALL_KIND_GIT, ""
