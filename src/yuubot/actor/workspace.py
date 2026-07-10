from pathlib import Path

from ..domain.records import ActorRecord


def resolve_workspace_path(
    raw: str,
    workspace_dir: Path,
    actor_id: str,
) -> Path:
    if not raw.strip():
        return (workspace_dir / actor_id).resolve()
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (workspace_dir / path).resolve()


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
                "- Put one-time reports, pages, charts, and exports in `artifacts/<slug>/`.",
                "- `uploads/`: uploaded files grouped by MIME type.",
                "- Put code and documentation with an ongoing maintenance lifecycle in `projects/<slug>/`.",
                "- `notes/`: actor notes.",
                "- `scripts/`: helper scripts.",
                "- Keep implementation files inside their artifact or project directory; reserve the workspace root for this map and established entry points.",
                "- Keep this AGENTS.md concise as the workspace map. Store project details, run instructions, and design notes with the corresponding project.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def resolve_actor_workspace_path(
    actor_id: str,
    live_workspace: str | None,
    record: ActorRecord | None,
    default_workspace_dir: Path,
) -> Path | None:
    raw = live_workspace
    if raw is None:
        if record is None:
            return None
        raw = record.workspace
    return resolve_workspace_path(raw or "", default_workspace_dir, actor_id)
