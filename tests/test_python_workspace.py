from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from yuubot.python import workspace as workspace_module


async def test_workspace_venv_requires_ready_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("# partial\n", encoding="utf-8")
    calls = 0

    async def fake_create_subprocess_exec(*args: object, **kwargs: Any) -> object:
        del args
        nonlocal calls
        calls += 1
        root = Path(kwargs["cwd"])

        class Process:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                (root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
                (root / ".venv" / "bin" / "python").write_text("# ready\n", encoding="utf-8")
                return b"", b""

            def kill(self) -> None:
                return None

        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    assert await workspace_module.ensure_workspace_venv(tmp_path) == python
    assert calls == 1
    assert (tmp_path / ".yuubot" / "venv.ready").is_file()


async def test_workspace_venv_sync_is_serialized_per_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_create_subprocess_exec(*args: object, **kwargs: Any) -> object:
        del args
        nonlocal calls
        calls += 1
        root = Path(kwargs["cwd"])

        class Process:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                await asyncio.sleep(0.01)
                (root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
                (root / ".venv" / "bin" / "python").write_text("# ready\n", encoding="utf-8")
                return b"", b""

            def kill(self) -> None:
                return None

        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    first, second = await asyncio.gather(
        workspace_module.ensure_workspace_venv(tmp_path),
        workspace_module.ensure_workspace_venv(tmp_path),
    )

    assert first == second == tmp_path / ".venv" / "bin" / "python"
    assert calls == 1


async def test_workspace_dependency_update_invalidates_ready_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("# ready\n", encoding="utf-8")
    marker = tmp_path / ".yuubot" / "venv.ready"
    marker.parent.mkdir(parents=True)
    marker.write_text("ok\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """[project]
name = "yuubot-workspace"
version = "0.0.0"
requires-python = ">=3.14"
dependencies = [
    "ipykernel>=7.3.0",
]
""",
        encoding="utf-8",
    )
    calls = 0

    async def fake_create_subprocess_exec(*args: object, **kwargs: Any) -> object:
        del args, kwargs
        nonlocal calls
        calls += 1

        class Process:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

            def kill(self) -> None:
                return None

        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    assert await workspace_module.ensure_workspace_venv(tmp_path) == python

    assert calls == 1
    assert marker.read_text(encoding="utf-8") == "ok\n"
    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert pyproject.startswith("[project]\n")
    assert '"strip-ansi>=0.1.1"' in pyproject


async def test_workspace_dependency_update_repairs_broken_template_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("# ready\n", encoding="utf-8")
    (tmp_path / ".yuubot").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        """[project    "strip-ansi>=0.1.1",
]
name = "yuubot-workspace"
dependencies = [
    "ipykernel>=7.3.0",
]
""",
        encoding="utf-8",
    )

    async def fake_create_subprocess_exec(*args: object, **kwargs: Any) -> object:
        del args, kwargs

        class Process:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b""

            def kill(self) -> None:
                return None

        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    assert await workspace_module.ensure_workspace_venv(tmp_path) == python

    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert pyproject.startswith("[project]\n")
    assert '"strip-ansi>=0.1.1"' in pyproject


async def test_workspace_venv_failure_clears_ready_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / ".yuubot" / "venv.ready"
    marker.parent.mkdir(parents=True)
    marker.write_text("ok\n", encoding="utf-8")

    async def fake_create_subprocess_exec(*args: object, **kwargs: Any) -> object:
        del args, kwargs

        class Process:
            returncode = 1

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"", b"broken"

            def kill(self) -> None:
                return None

        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError, match="uv sync failed"):
        await workspace_module.ensure_workspace_venv(tmp_path)

    assert not marker.exists()
