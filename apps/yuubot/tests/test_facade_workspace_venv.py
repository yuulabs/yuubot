"""Facade workspace venv provisioning — isolated Python environment per actor."""

from __future__ import annotations

import subprocess
from pathlib import Path

from yuubot.core.facade.workspace import FacadeEndpoint, FacadeWorkspace


def test_bind_actor_provisions_isolated_venv(tmp_path: Path) -> None:
    ws = FacadeWorkspace(root=tmp_path, package_name="yext")
    binding = ws.bind_actor(
        actor_id="actor-1",
        agent_name="a",
        session_id="s",
        mailbox_id="m",
        capabilities=(),
        endpoint=FacadeEndpoint(host="127.0.0.1", port=1, token="t"),
    )

    assert binding.venv_python is not None
    venv_python = Path(binding.venv_python)
    assert venv_python.exists(), f"venv python not found at {venv_python}"
    assert ".venv" in venv_python.parts

    out = subprocess.run(
        [str(venv_python), "-c", "import pandas, numpy, matplotlib; print('ok')"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr


def test_bind_actor_is_idempotent(tmp_path: Path) -> None:
    """Re-binding an already-provisioned actor must not re-run uv sync."""
    ws = FacadeWorkspace(root=tmp_path, package_name="yext")
    endpoint = FacadeEndpoint(host="127.0.0.1", port=1, token="t")

    first = ws.bind_actor(
        actor_id="actor-2",
        agent_name="a",
        session_id="s1",
        mailbox_id="m",
        capabilities=(),
        endpoint=endpoint,
    )
    assert first.venv_python is not None
    venv_python = Path(first.venv_python)
    venv_mtime_before = venv_python.stat().st_mtime_ns

    # Delete the pyproject.toml — if uv sync re-runs, it would be rewritten.
    pyproject = first.root / "pyproject.toml"
    assert pyproject.exists()
    original_pyproject = pyproject.read_text(encoding="utf-8")
    pyproject.unlink()

    second = ws.bind_actor(
        actor_id="actor-2",
        agent_name="a",
        session_id="s2",
        mailbox_id="m",
        capabilities=(),
        endpoint=endpoint,
    )

    # Same venv python path, venv not rebuilt
    assert second.venv_python == first.venv_python
    assert venv_python.exists()
    assert venv_python.stat().st_mtime_ns == venv_mtime_before
    # pyproject.toml must NOT have been rewritten on the idempotent path
    assert not pyproject.exists(), "idempotent re-bind must not rewrite pyproject.toml"
    # restore for cleanliness
    pyproject.write_text(original_pyproject, encoding="utf-8")
