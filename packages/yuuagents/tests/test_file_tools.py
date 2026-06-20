from __future__ import annotations

import base64
from pathlib import Path

import pytest
import yuullm

from yuuagents.agent.actor import _render_task_result
from yuuagents.core.task import Owner, OwnerType, Task, TaskStatus
from yuuagents.tool.files import FileToolConfig, WorkspaceFiles


def test_workspace_files_read_text(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    files = WorkspaceFiles.from_config(
        FileToolConfig(workspace_root=str(tmp_path)),
    )

    assert files.read("notes.txt") == "hello\n"


def test_workspace_files_read_image_as_multimodal_content(tmp_path: Path) -> None:
    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    (tmp_path / "pixel.png").write_bytes(image_bytes)
    files = WorkspaceFiles.from_config(
        FileToolConfig(workspace_root=str(tmp_path)),
    )

    result = files.read("pixel.png")

    assert isinstance(result, list)
    assert result[0]["type"] == "text"
    assert result[1]["type"] == "image_url"
    assert yuullm.is_image_item(result[1])
    assert result[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_workspace_files_edit_requires_exactly_one_match(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("same\nsame\n", encoding="utf-8")
    files = WorkspaceFiles.from_config(
        FileToolConfig(workspace_root=str(tmp_path)),
    )

    with pytest.raises(ValueError, match="expected exactly 1"):
        files.edit(raw_path="notes.txt", old_string="same", new_string="next")


def test_workspace_files_edit_and_write_stay_inside_workspace(tmp_path: Path) -> None:
    files = WorkspaceFiles.from_config(
        FileToolConfig(workspace_root=str(tmp_path)),
    )

    assert files.write(raw_path="dir/notes.txt", content="before") == (
        "Wrote dir/notes.txt."
    )
    assert files.edit(
        raw_path="dir/notes.txt",
        old_string="before",
        new_string="after",
    ) == "Edited dir/notes.txt."
    assert (tmp_path / "dir/notes.txt").read_text(encoding="utf-8") == "after"

    with pytest.raises(ValueError, match="escapes workspace"):
        files.write(raw_path="../outside.txt", content="nope")


def test_actor_result_renderer_preserves_multimodal_tool_output() -> None:
    output: yuullm.Content = [
        {"type": "text", "text": "image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]
    task: Task[yuullm.ToolOutput] = Task(
        id="task-1",
        owner=Owner(type=OwnerType.AGENT, id="agent-1"),
        status=TaskStatus.COMPLETED,
        result=output,
    )

    assert _render_task_result(task) is output
