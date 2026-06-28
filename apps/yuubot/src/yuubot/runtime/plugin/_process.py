"""External plugin subprocess lifecycle: spawn, health-check, stop."""

from __future__ import annotations

import asyncio
import logging
import secrets
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from yuubot.resources.records import IntegrationRecord

from ._manifest import ExternalPluginError

logger = logging.getLogger(__name__)

PLUGIN_TOKEN_CONFIG_KEY = "_plugin_token"

# ── Status / process dataclasses ────────────────────────────────────


@dataclass
class ExternalPluginStatus:
    name: str
    integration_id: str
    port: int
    healthy: bool = False
    pid: int | None = None


@dataclass
class ExternalPluginProcess:
    integration_id: str
    name: str
    port: int
    process: asyncio.subprocess.Process
    plugin_token: str
    internal_token: str

    def status(self) -> ExternalPluginStatus:
        return ExternalPluginStatus(
            name=self.name,
            integration_id=self.integration_id,
            port=self.port,
            healthy=self.process.returncode is None,
            pid=self.process.pid,
        )


# ── Port / python / env helpers ─────────────────────────────────────


def allocate_port() -> int:
    """Bind to an ephemeral port and return it (port released immediately)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return cast(int, sock.getsockname()[1])


def plugin_python(plugin_dir: Path) -> Path:
    python = plugin_dir / ".venv" / "bin" / "python"
    if python.exists():
        return python
    return Path(sys.executable)


def plugin_token(record: IntegrationRecord) -> str:
    value = record.config.get(PLUGIN_TOKEN_CONFIG_KEY)
    if isinstance(value, str) and value:
        return value
    return secrets.token_urlsafe(24)


# ── Subprocess execution ────────────────────────────────────────────


async def run_subprocess(args: tuple[str, ...], *, cwd: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()
        raise ExternalPluginError(f"{' '.join(args)} failed: {detail}")


# ── Health checking ─────────────────────────────────────────────────


async def wait_for_plugin_health(
    running: ExternalPluginProcess,
    *,
    timeout_s: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if running.process.returncode is not None:
            raise ExternalPluginError(
                f"plugin {running.name!r} exited with code {running.process.returncode}",
            )
        if await asyncio.to_thread(health_check_sync, running.port):
            return
        await asyncio.sleep(0.05)
    raise ExternalPluginError(f"plugin {running.name!r} did not become healthy")


def health_check_sync(port: int) -> bool:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/health", method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=0.5) as response:
            response.read()
            return response.status == 200
    except urllib.error.URLError:
        return False
    except Exception as exc:
        logger.debug(
            "unexpected error during plugin health check on port %d: %s", port, exc,
        )
        return False
