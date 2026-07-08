"""Offline update script generation and scheduling."""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .install import install_deps_script, project_root
from .types import UpdateApplyResult


def build_apply_script(
    root: Path,
    config_path: Path,
    host: str,
    port: int,
    log_path: Path,
    skip_web_build: bool,
) -> str:
    root_q = shlex.quote(str(root))
    config_q = shlex.quote(str(config_path))
    host_q = shlex.quote(host)
    port_q = shlex.quote(str(port))
    log_q = shlex.quote(str(log_path))
    install_q = shlex.quote(str(install_deps_script(root)))
    skip_flag = " --skip-web-build" if skip_web_build else ""
    return f"""#!/usr/bin/env bash
set -euo pipefail
exec > >(tee -a {log_q}) 2>&1
sleep 2
cd {root_q}
git pull --ff-only
{install_q}{skip_flag}
uv run ybot migrate {config_q}
exec uv run ybot serve {config_q} --host {host_q} --port {port_q}
"""


def schedule_apply(
    *,
    root: Path | None = None,
    config_path: Path,
    data_dir: Path,
    host: str,
    port: int,
    skip_web_build: bool = False,
    on_shutdown: Callable[[], None] | None = None,
) -> UpdateApplyResult:
    resolved_root = root or project_root()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tmp_dir = data_dir / "tmp"
    logs_dir = data_dir / "logs"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    script_path = tmp_dir / f"yuubot-update-{stamp}.sh"
    log_path = logs_dir / f"update-{stamp}.log"
    script_path.write_text(
        build_apply_script(
            resolved_root,
            config_path,
            host,
            port,
            log_path,
            skip_web_build,
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o750)
    subprocess.Popen(
        ["/bin/bash", str(script_path)],
        cwd=resolved_root,
        start_new_session=True,
        env=os.environ.copy(),
    )
    if on_shutdown is not None:
        on_shutdown()
    return UpdateApplyResult("scheduled", str(log_path), "update scheduled")
