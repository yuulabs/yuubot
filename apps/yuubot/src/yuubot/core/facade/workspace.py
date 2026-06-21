"""Facade workspace management -- actor-local context bindings."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from yuubot.core.capabilities import AnyCapabilitySpec
from yuubot.core.facade.context import FACADE_CONTEXT_MODULE, render_context_module

YEXT_PACKAGE = "yext"


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
        return ActorFacadeBinding(
            actor_id=actor_id,
            agent_name=agent_name,
            session_id=session_id,
            mailbox_id=mailbox_id,
            capabilities=visible_capabilities,
            root=actor_root,
            sys_path=[str(actor_root)],
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
        )

    def cleanup_actor(self, actor_id: str) -> None:
        from yuubot.core.actors.workspace import safe_actor_path_id

        path_id = safe_actor_path_id(actor_id)
        _replace_path(self.root / "actors" / path_id)


def _replace_dir(path: Path) -> None:
    _replace_path(path)
    path.mkdir(parents=True, exist_ok=True)


def _replace_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path)
