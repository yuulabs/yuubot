"""Conversation workspace path resolution and escape validation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from yuubot.core.bindings import AgentBinding
from yuubot.core.conversations import ConversationManager, ConversationStore
from yuubot.core.validation import GenerationParams
from yuubot.resources.records import (
    ActorRecord,
    BudgetPolicy,
    CapabilitySetRecord,
    LLMBackendRecord,
    ModelCapabilities,
    ModelConfig,
    Pricing,
    RunBudget,
)

if TYPE_CHECKING:
    pass


class TestConversationWorkspaceResolution:
    """Tests for ConversationManager._resolve_workspace_path()."""

    def test_creates_directory(self, tmp_path: Path) -> None:
        workspace_root = tmp_path / "workspace"
        manager = _make_manager(workspace_root=workspace_root)

        resolved = manager._resolve_workspace_path("test")

        expected = (workspace_root / "test").resolve()
        assert resolved == expected
        assert resolved.exists()
        assert resolved.is_dir()

    def test_rejects_escape(self, tmp_path: Path) -> None:
        workspace_root = tmp_path / "workspace"
        manager = _make_manager(workspace_root=workspace_root)

        with pytest.raises(ValueError, match="escapes workspace root"):
            manager._resolve_workspace_path("../../etc/passwd")

        with pytest.raises(ValueError, match="escapes workspace root"):
            manager._resolve_workspace_path("/etc/passwd")

        with pytest.raises(ValueError, match="escapes workspace root"):
            manager._resolve_workspace_path("../../")

    def test_none_or_empty_returns_none(self, tmp_path: Path) -> None:
        workspace_root = tmp_path / "workspace"
        manager = _make_manager(workspace_root=workspace_root)

        assert manager._resolve_workspace_path(None) is None
        assert manager._resolve_workspace_path("") is None
        assert manager._resolve_workspace_path("  ") is None

    def test_subdirectory_within_root(self, tmp_path: Path) -> None:
        workspace_root = tmp_path / "workspace"
        manager = _make_manager(workspace_root=workspace_root)

        resolved = manager._resolve_workspace_path("project-a/subproject")

        expected = (workspace_root / "project-a" / "subproject").resolve()
        assert resolved == expected
        assert resolved.exists()
        assert resolved.is_dir()


class TestRequireWorkspacePath:
    """Tests for AgentBinding.require_workspace_path() — no fallback behavior."""

    def test_no_fallback_to_capability_set(self) -> None:
        cap_set = CapabilitySetRecord(
            name="test-cap",
            workspace_path="/nonexistent/absolute/path",
        )
        binding = AgentBinding(
            owner_id="test-owner",
            agent_name="test-agent",
            actor=_dummy_actor(),
            capability_set=cap_set,
            llm=_dummy_llm(),
            budget=_dummy_budget(),
            workspace_path=None,
        )

        with pytest.raises(RuntimeError, match="no workspace path"):
            binding.require_workspace_path()

    def test_returns_workspace_path_when_set(self, tmp_path: Path) -> None:
        cap_set = CapabilitySetRecord(
            name="test-cap",
            workspace_path="/nonexistent/absolute/path",
        )
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)

        binding = AgentBinding(
            owner_id="test-owner",
            agent_name="test-agent",
            actor=_dummy_actor(),
            capability_set=cap_set,
            llm=_dummy_llm(),
            budget=_dummy_budget(),
            workspace_path=workspace,
        )

        assert binding.require_workspace_path() == workspace

    def test_raises_when_workspace_path_is_none_and_no_capability_set_path(
        self,
    ) -> None:
        cap_set = CapabilitySetRecord(name="test-cap")
        assert cap_set.workspace_path == ""  # default

        binding = AgentBinding(
            owner_id="test-owner",
            agent_name="test-agent",
            actor=_dummy_actor(),
            capability_set=cap_set,
            llm=_dummy_llm(),
            budget=_dummy_budget(),
            workspace_path=None,
        )

        with pytest.raises(RuntimeError, match="no workspace path"):
            binding.require_workspace_path()


# ── Helpers ──────────────────────────────────────────────────────


def _make_manager(*, workspace_root: Path) -> ConversationManager:
    store = ConversationStore(store=AsyncMock())
    repository = AsyncMock()
    python_sessions = AsyncMock()
    llm_session_factory_factory = AsyncMock()

    return ConversationManager(
        store=store,
        repository=repository,
        python_sessions=python_sessions,
        llm_session_factory_factory=llm_session_factory_factory,
        workspace_root=workspace_root,
    )


def _dummy_actor() -> ActorRecord:
    return ActorRecord(
        name="test-actor",
        persona_prompt="",
        capability_set_id="test-cap",
        llm_backend_id="test-backend",
        model="gpt-4",
    )


def _dummy_llm():
    from yuubot.core.llm import BoundLLM

    backend = LLMBackendRecord(
        name="test-backend",
        provider_identity="openai",
        model_configs={
            "gpt-4": ModelConfig(
                pricing=Pricing(),
                capabilities=ModelCapabilities(),
            )
        },
        budget=BudgetPolicy(),
    )
    return BoundLLM(
        backend=backend,
        model="gpt-4",
        generation_params=GenerationParams(),
    )


def _dummy_budget() -> RunBudget:
    return RunBudget()
