from pathlib import Path


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
