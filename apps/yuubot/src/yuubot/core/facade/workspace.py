"""Facade workspace management -- actor-local context bindings."""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from yuubot.core.capabilities import AnyCapabilitySpec
from yuubot.core.facade.context import FACADE_CONTEXT_MODULE, render_context_module
from yuubot.core.integrations.contracts import VisibleIntegrationSurface

YEXT_PACKAGE = "yext"

logger = logging.getLogger(__name__)


@dataclass
class FacadeEndpoint:
    host: str
    port: int
    token: str


@dataclass
class ActorFacadeBinding:
    actor_id: str
    agent_name: str
    session_id: str
    mailbox_id: str
    integration_surfaces: tuple[VisibleIntegrationSurface, ...]
    root: Path
    sys_path: list[str]
    startup_code: str
    session_state: dict[str, object]
    venv_python: str | None = None


@dataclass
class FacadeWorkspace:
    """Owns actor-specific facade context bindings."""

    root: Path
    package_name: str = YEXT_PACKAGE

    def generate_catalog(
        self,
        capabilities: Iterable[AnyCapabilitySpec],
    ) -> Path:
        _ = tuple(capabilities)
        catalog_root = self.root / "catalog"
        _replace_dir(catalog_root)
        return catalog_root

    def bind_actor(
        self,
        *,
        actor_id: str,
        agent_name: str,
        session_id: str,
        mailbox_id: str,
        surfaces: Iterable[VisibleIntegrationSurface],
        endpoint: FacadeEndpoint,
    ) -> ActorFacadeBinding:
        from yuubot.core.actors.workspace import safe_actor_path_id

        path_id = safe_actor_path_id(actor_id)
        actor_root = self.root / "actors" / path_id
        visible_surfaces = tuple(surfaces)

        actor_root.mkdir(parents=True, exist_ok=True)
        (actor_root / f"{FACADE_CONTEXT_MODULE}.py").write_text(
            render_context_module(
                actor_id=actor_id,
                agent_name=agent_name,
                session_id=session_id,
                mailbox_id=mailbox_id,
                host=endpoint.host,
                port=endpoint.port,
                token=endpoint.token,
            ),
            encoding="utf-8",
        )
        venv_python = _provision_workspace_venv(actor_root)
        daemon_src = _resolve_daemon_facade_src()
        sys_path: list[str] = []
        if daemon_src is not None:
            sys_path.append(str(daemon_src))
        sys_path.append(str(actor_root))
        # The system facade (yb/tim) + per-integration facade context is the
        # kernel bootstrap; integration ``yext.*`` modules are derived from
        # each visible surface's ``sdk.import_paths`` by
        # ``ExecutePythonToolFactory.derive`` (§6.6), so do NOT hardcode any
        # integration import here.
        return ActorFacadeBinding(
            actor_id=actor_id,
            agent_name=agent_name,
            session_id=session_id,
            mailbox_id=mailbox_id,
            integration_surfaces=visible_surfaces,
            root=actor_root,
            sys_path=sys_path,
            startup_code=(
                "import yb\n"
                "import tim\n"
                f"import {FACADE_CONTEXT_MODULE} as facade_context"
            ),
            session_state={
                "actor_id": actor_id,
                "agent_name": agent_name,
                "session_id": session_id,
                "mailbox_id": mailbox_id,
                "yb_package": "yb",
                "yext_package": self.package_name,
            },
            venv_python=venv_python,
        )

    def cleanup_actor(self, actor_id: str) -> None:
        from yuubot.core.actors.workspace import safe_actor_path_id

        path_id = safe_actor_path_id(actor_id)
        _replace_path(self.root / "actors" / path_id)


@lru_cache(maxsize=1)
def _read_actor_pyproject_template() -> str:
    """Read the static ``actor_pyproject.toml`` shipped with this package.

    The template is the single source of truth for the actor venv's
    dependency declaration (lower bounds only; agents may ``uv add`` to
    pin stricter). Reading it via ``importlib.resources`` resolves both
    editable installs (source tree) and wheel installs (template included
    via ``[tool.hatch.build.targets.wheel.force-include]``). The result is
    cached (``lru_cache``) so repeated ``bind_actor`` calls do not re-read
    the file.
    """
    return (
        files("yuubot.core.facade") / "actor_pyproject.toml"
    ).read_text(encoding="utf-8")


def _provision_workspace_venv(actor_root: Path) -> str:
    """Provision an isolated ``.venv`` in *actor_root* via ``uv sync``.

    Idempotent: if ``actor_root/.venv/bin/python`` already exists AND the
    on-disk ``pyproject.toml`` is byte-identical to the static
    ``actor_pyproject.toml`` template shipped with the daemon, returns
    immediately without touching the workspace. This fast path means a
    no-op re-bind (same template) never re-runs ``uv sync``.

    On first provisioning, or when the shipped template has changed since
    the venv was last provisioned (e.g. the daemon was upgraded to bump
    a facade dep baseline), writes the current template content to the
    workspace ``pyproject.toml`` and runs ``uv sync`` so the existing
    venv is reconciled to the new declaration.
    """
    venv_python = actor_root / ".venv" / "bin" / "python"
    pyproject = actor_root / "pyproject.toml"

    template_content = _read_actor_pyproject_template()

    if (
        venv_python.exists()
        and pyproject.exists()
        and pyproject.read_text(encoding="utf-8") == template_content
    ):
        return str(venv_python)

    pyproject.write_text(template_content, encoding="utf-8")

    result = subprocess.run(
        ["uv", "sync"],
        cwd=actor_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"uv sync failed in {actor_root} (exit {result.returncode}):\n"
            f"{result.stderr}"
        )
    if not venv_python.exists():
        raise RuntimeError(
            f"uv sync did not produce {venv_python} in {actor_root}"
        )
    return str(venv_python)


def _resolve_daemon_facade_src() -> Path | None:
    """Absolute path of the daemon's ``apps/yuubot/src`` directory.

    The facade source (``yb``, ``tim``, ``yext``, ``yuubot``) lives at
    ``apps/yuubot/src`` and is exposed in the daemon process only via the
    editable ``.pth`` — the isolated actor venv does not have it. The kernel
    bootstrap runs ``import yb; import tim; import facade_context`` (the facade
    ``startup_code``); integration ``yext.*`` modules are imported by the agent
    on demand or surfaced through ``PythonRuntime.imports``. The binding's
    ``sys_path`` must include this dir so those modules import from the same
    source the daemon runs.

    Resolution uses the daemon's own editably-imported ``yb``: its ``__file__``
    sits at ``apps/yuubot/src/yb/<...>.py``, so ``parent.parent`` is
    ``apps/yuubot/src``. Both ``yb`` and ``yuubot`` and ``tim`` and ``yext``
    live as siblings under that dir, so resolving from ``yb`` covers all of
    them. If ``yb`` is not importable in this process (e.g. running outside the
    daemon), return ``None`` — the kernel will then fail to import ``yb``,
    which is an honest environment error to surface, not a provisioning bug.
    Do not hardcode paths and do not make this overridable via config.
    """
    try:
        import yb
    except ImportError:
        logger.warning(
            "could not resolve daemon facade src: 'yb' is not importable in "
            "this process; facade imports will not resolve on the actor venv"
        )
        return None
    return Path(yb.__file__).resolve().parent.parent


def _replace_dir(path: Path) -> None:
    _replace_path(path)
    path.mkdir(parents=True, exist_ok=True)


def _replace_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path)
