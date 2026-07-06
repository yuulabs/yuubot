from pathlib import Path

from yuubot.actor.workspace import resolve_actor_workspace_path, resolve_workspace_path
from yuubot.domain.records import ActorRecord, ModelCard


def _record(*, actor_id: str = "amy", workspace: str = "") -> ActorRecord:
    return ActorRecord(
        id=actor_id,
        name="Amy",
        workspace=workspace,
        model=ModelCard(selector="fake"),
        provider="fake",
    )


def test_resolve_workspace_path_defaults_to_actor_id_under_workspace_dir(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    assert resolve_workspace_path("", workspace_dir=workspace_dir, actor_id="amy") == (workspace_dir / "amy").resolve()


def test_resolve_workspace_path_joins_relative_paths_under_workspace_dir(tmp_path: Path, monkeypatch) -> None:
    workspace_dir = tmp_path / "workspace"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert resolve_workspace_path("amy", workspace_dir=workspace_dir, actor_id="amy") == (workspace_dir / "amy").resolve()


def test_resolve_workspace_path_keeps_absolute_paths(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    absolute = tmp_path / "custom"
    assert resolve_workspace_path(str(absolute), workspace_dir=workspace_dir, actor_id="amy") == absolute.resolve()


def test_resolve_actor_workspace_path_uses_record_workspace(tmp_path: Path, monkeypatch) -> None:
    workspace_dir = tmp_path / "workspace"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    record = _record(workspace="amy")
    assert resolve_actor_workspace_path(
        "amy",
        live_workspace=None,
        record=record,
        default_workspace_dir=workspace_dir,
    ) == (workspace_dir / "amy").resolve()


def test_resolve_actor_workspace_path_prefers_live_workspace(tmp_path: Path, monkeypatch) -> None:
    workspace_dir = tmp_path / "workspace"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    record = _record(workspace="other")
    assert resolve_actor_workspace_path(
        "amy",
        live_workspace="amy",
        record=record,
        default_workspace_dir=workspace_dir,
    ) == (workspace_dir / "amy").resolve()
