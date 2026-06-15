"""External plugin install and environment setup."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from ._manifest import (
    ExternalPluginError,
    ExternalPluginManifest,
    _plugin_root_from_archive,
    load_external_plugin_manifest,
)
from ._process import plugin_python, run_subprocess


def copy_plugin_source(
    plugins_dir: Path,
    source: Path,
) -> tuple[Path, ExternalPluginManifest]:
    """Copy a plugin directory or zip to *plugins_dir*, return (target, manifest)."""
    source = source.expanduser().resolve()
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "plugin"
        if source.is_dir():
            shutil.copytree(source, staging)
        elif zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as archive:
                archive.extractall(staging)
            staging = _plugin_root_from_archive(staging)
        else:
            raise ExternalPluginError(
                "source must be a plugin directory or zip file",
            )

        manifest = load_external_plugin_manifest(staging)
        target = plugins_dir / manifest.name
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staging, target)
        return target, manifest


def check_system_requirements(manifest: ExternalPluginManifest) -> None:
    missing = [
        name for name in manifest.requires_system if shutil.which(name) is None
    ]
    if missing:
        raise ExternalPluginError(
            "missing required system commands: " + ", ".join(sorted(missing)),
        )


async def install_plugin_environment(plugin_dir: Path) -> None:
    """Create a venv and install plugin dependencies using uv."""
    await run_subprocess(("uv", "venv", ".venv"), cwd=plugin_dir)
    python = plugin_python(plugin_dir)
    await run_subprocess(
        ("uv", "pip", "install", ".", "--python", str(python)), cwd=plugin_dir,
    )
