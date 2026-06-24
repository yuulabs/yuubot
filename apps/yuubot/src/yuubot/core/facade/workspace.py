"""Facade workspace management -- actor-local context bindings."""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from yuubot.core.capabilities import AnyCapabilitySpec
from yuubot.core.facade.context import FACADE_CONTEXT_MODULE, render_context_module

YEXT_PACKAGE = "yext"

logger = logging.getLogger(__name__)

# Dependencies sourced from packages/yuuagents/pyproject.toml — these are
# the kernel deps the agent's ipykernel needs to start.
_YUUAGENTS_VENV_DEPS = ("ipykernel", "jupyter-client")
# Dependencies sourced from apps/yuubot/pyproject.toml — the research/analysis
# stack the version-真值 source pins, plus msgspec (the facade's only
# third-party dependency: yb/_client, yb/delegate, yb/schedule, yb/tasks,
# tim/_channel, yext/github, yuubot/core/facade/protocol import nothing else).
_YUUBOT_VENV_DEPS = ("pandas", "numpy", "matplotlib", "msgspec")

_VENV_PYPROJECT_TEMPLATE = """\
[project]
name = "actor-workspace"
version = "0"
requires-python = ">=3.11"
dependencies = [{deps}]
"""


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
    capabilities: tuple[AnyCapabilitySpec, ...]
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

    def generate_catalog(self, capabilities: Iterable[AnyCapabilitySpec]) -> Path:
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
        capabilities: Iterable[AnyCapabilitySpec],
        endpoint: FacadeEndpoint,
    ) -> ActorFacadeBinding:
        from yuubot.core.actors.workspace import safe_actor_path_id

        path_id = safe_actor_path_id(actor_id)
        actor_root = self.root / "actors" / path_id
        visible_capabilities = tuple(capabilities)

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
        return ActorFacadeBinding(
            actor_id=actor_id,
            agent_name=agent_name,
            session_id=session_id,
            mailbox_id=mailbox_id,
            capabilities=visible_capabilities,
            root=actor_root,
            sys_path=sys_path,
            startup_code=(
                "import yb\n"
                "import tim\n"
                f"import {self.package_name}.github\n"
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


def _provision_workspace_venv(actor_root: Path) -> str:
    """Provision an isolated .venv in *actor_root* via ``uv sync``.

    Idempotent: if ``actor_root/.venv/bin/python`` already exists AND the
    on-disk ``pyproject.toml`` matches the desired dependency set, returns
    immediately without touching the workspace. This fast path means a
    no-op re-bind (same deps) never re-runs ``uv sync``.

    On first provisioning, or when the declared deps have changed since the
    venv was last provisioned (e.g. the daemon was upgraded to add a new
    facade dep), writes the pinned ``pyproject.toml`` (version specifiers
    copied from the owning pyprojects — the daemon pyprojects are the 真值
    source) and runs ``uv sync`` so the existing venv is reconciled to the
    new declaration.
    """
    venv_python = actor_root / ".venv" / "bin" / "python"
    pyproject = actor_root / "pyproject.toml"

    deps = _resolve_workspace_pins()
    deps_lines = ", ".join(f'"{d}"' for d in deps)
    pyproject_content = _VENV_PYPROJECT_TEMPLATE.format(deps=deps_lines)

    if (
        venv_python.exists()
        and pyproject.exists()
        and pyproject.read_text(encoding="utf-8") == pyproject_content
    ):
        return str(venv_python)

    pyproject.write_text(pyproject_content, encoding="utf-8")

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


def _resolve_workspace_pins() -> list[str]:
    """Read version specifiers from the owning pyprojects.

    Pins are copied (not invented): ipykernel + jupyter-client from
    packages/yuuagents/pyproject.toml; pandas/numpy/matplotlib from
    apps/yuubot/pyproject.toml.  If a pin cannot be read, falls back to
    the bare name (uv resolves latest) and logs a warning.
    """
    pins: list[str] = []
    for pyproject_path, deps in (
        (_yuuagents_pyproject_path(), _YUUAGENTS_VENV_DEPS),
        (_yuubot_pyproject_path(), _YUUBOT_VENV_DEPS),
    ):
        specs = _read_dependency_specs(pyproject_path)
        for dep in deps:
            pin = specs.get(dep)
            if pin is not None:
                pins.append(pin)
            else:
                logger.warning(
                    "could not read pin for %s from %s; falling back to bare name",
                    dep,
                    pyproject_path,
                )
                pins.append(dep)
    return pins


def _read_dependency_specs(pyproject_path: Path) -> dict[str, str]:
    """Parse a pyproject.toml and return {name: PEP-508-spec}.

    The returned value is the raw dependency string (e.g. ``"pandas>=2.0"``
    or ``"ipykernel>=7.0.0"``) so uv can resolve from the same constraint.
    """
    import tomllib

    if not pyproject_path.exists():
        return {}
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    deps_list = data.get("project", {}).get("dependencies", [])
    specs: dict[str, str] = {}
    for entry in deps_list:
        name = _dep_name(entry)
        if name:
            specs[name] = entry
    return specs


def _dep_name(entry: str) -> str:
    """Extract the canonical (lower-cased, no extra) name from a PEP-508 spec."""
    name = entry.strip()
    for sep in (">", "<", "=", "!", "~", ";", "[", " "):
        idx = name.find(sep)
        if idx != -1:
            name = name[:idx]
    return name.strip().lower()


def _yuuagents_pyproject_path() -> Path:
    return Path(__file__).resolve().parents[6] / "packages" / "yuuagents" / "pyproject.toml"


def _yuubot_pyproject_path() -> Path:
    return Path(__file__).resolve().parents[4] / "pyproject.toml"


def _resolve_daemon_facade_src() -> Path | None:
    """Absolute path of the daemon's ``apps/yuubot/src`` directory.

    The facade source (``yb``, ``tim``, ``yext``, ``yuubot``) lives at
    ``apps/yuubot/src`` and is exposed in the daemon process only via the
    editable ``.pth`` — the isolated actor venv does not have it. The kernel
    bootstrap runs ``import yb; import tim; import yext.github`` (the facade
    ``startup_code``), so the binding's ``sys_path`` must include this dir so
    those modules import from the same source the daemon runs.

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
