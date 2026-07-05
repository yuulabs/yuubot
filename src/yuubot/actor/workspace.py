from pathlib import Path

from ..domain.records import ActorRecord


def prepare_workspace(path: Path) -> None:
    workspace = path.resolve()
    for dirname in [".agents/skills", "artifacts", "uploads", "projects", "notes", "scripts"]:
        (workspace / dirname).mkdir(parents=True, exist_ok=True)
    agents = workspace / "AGENTS.md"
    if agents.exists():
        return
    agents.write_text(
        "\n".join(
            [
                "# Workspace",
                "",
                "- `artifacts/`: user-visible outputs.",
                "- `uploads/`: uploaded files grouped by MIME type.",
                "- `projects/`: actor-managed project files.",
                "- `notes/`: actor notes.",
                "- `scripts/`: helper scripts.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def resolve_actor_workspace_path(
    actor_id: str,
    *,
    live_workspace: str | None,
    record: ActorRecord | None,
    default_workspace_dir: Path,
) -> Path | None:
    if live_workspace is not None:
        return Path(live_workspace).resolve()
    if record is None:
        return None
    return Path(record.workspace or str(default_workspace_dir / actor_id)).resolve()
