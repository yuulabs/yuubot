"""Facade workspace management — directory layout, symlinks, and actor bindings."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from yuubot.core.capabilities import AnyCapabilitySpec
from yuubot.core.facade.client import YEXT_CONTEXT_MODULE
from yuubot.core.facade.codegen import (
    YEXT_PACKAGE,
    clear_facade_module_cache,
    render_context_module,
    write_facade_package,
)


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
    root: Path
    sys_path: list[str]
    startup_code: str
    session_state: dict[str, object]


@dataclass
class FacadeWorkspace:
    """Owns generated yext packages and actor-specific facade bindings."""

    root: Path
    package_name: str = YEXT_PACKAGE

    def generate_catalog(self, capabilities: Iterable[AnyCapabilitySpec]) -> Path:
        catalog_root = self.root / "catalog"
        _replace_dir(catalog_root)
        write_facade_package(
            catalog_root,
            capabilities=capabilities,
            package_name=self.package_name,
        )
        clear_facade_module_cache(self.package_name)
        return catalog_root / self.package_name

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
        source_root = self.root / "actor-packages" / path_id

        _replace_dir(source_root)
        write_facade_package(
            source_root,
            capabilities=capabilities,
            package_name=self.package_name,
        )
        clear_facade_module_cache(self.package_name)
        actor_root.mkdir(parents=True, exist_ok=True)
        _replace_path(actor_root / self.package_name)
        (actor_root / self.package_name).symlink_to(
            source_root / self.package_name,
            target_is_directory=True,
        )
        (actor_root / f"{YEXT_CONTEXT_MODULE}.py").write_text(
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
            root=actor_root,
            sys_path=[str(actor_root)],
            startup_code=(
                f"import {self.package_name}\n"
                f"import {YEXT_CONTEXT_MODULE} as yext_context"
            ),
            session_state={
                "actor_id": actor_id,
                "agent_name": agent_name,
                "session_id": session_id,
                "mailbox_id": mailbox_id,
                "facade_package": self.package_name,
            },
        )

    def cleanup_actor(self, actor_id: str) -> None:
        from yuubot.core.actors.workspace import safe_actor_path_id

        path_id = safe_actor_path_id(actor_id)
        _replace_path(self.root / "actors" / path_id)
        _replace_path(self.root / "actor-packages" / path_id)


def _replace_dir(path: Path) -> None:
    _replace_path(path)
    path.mkdir(parents=True, exist_ok=True)


def _replace_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path)
