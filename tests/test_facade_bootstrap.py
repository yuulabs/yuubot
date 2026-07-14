from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from yuubot.python.facade_imports import (
    ALL_FACADE_PACKAGES,
    facade_bootstrap_code,
    facade_bootstrap_module_source,
)
from yuubot.python.worker import KernelWorker
from yuubot.python.workspace import ensure_workspace_venv, prepare_kernel_workspace
from yuubot.runtime.pty_display import filter_tool_output


def test_facade_package_and_bootstrap_generation_are_consistent(tmp_path: Path) -> None:
    assert "yext.opencode" in ALL_FACADE_PACKAGES
    assert "yext.codex" in ALL_FACADE_PACKAGES
    assert "yext.github" in ALL_FACADE_PACKAGES
    assert "yext.web" in ALL_FACADE_PACKAGES
    assert "yb.tasks" in ALL_FACADE_PACKAGES
    assert "yb.fixer" in ALL_FACADE_PACKAGES
    assert "yb.conversations" in ALL_FACADE_PACKAGES
    assert "yb.mcps" in ALL_FACADE_PACKAGES
    assert "yb.skills" in ALL_FACADE_PACKAGES
    assert "yb.office.pdf" in ALL_FACADE_PACKAGES
    code = facade_bootstrap_code()
    for package in ALL_FACADE_PACKAGES:
        assert f"import {package}\n" in code

    prepare_kernel_workspace(tmp_path)
    source = (tmp_path / ".yuubot" / "facade_bootstrap.py").read_text(encoding="utf-8")
    assert source == facade_bootstrap_module_source()
    assert "import yext.opencode" in source
    assert "run_cell(_BOOTSTRAP" in source


@pytest.mark.asyncio
async def test_kernel_bootstrap_exposes_yext_opencode_without_manual_import(tmp_path: Path) -> None:
    prepare_kernel_workspace(tmp_path)
    await ensure_workspace_venv(tmp_path)
    worker = await KernelWorker.start(
        workspace=tmp_path,
        env={},
        max_rss_bytes=2**30,
        max_output_bytes=262144,
        execution_timeout_s=30.0,
    )
    try:
        output = await worker.run_code("yext.opencode.__name__")
        assert "yext.opencode" in output

        status_output = await worker.run_code("await yext.opencode.status()")
        assert "Status" in status_output

        await worker.reset_or_recycle()
        after_reset = await worker.run_code("yext.opencode.__name__")
        assert "yext.opencode" in after_reset
    finally:
        await worker.shutdown()


@pytest.mark.asyncio
async def test_cancelled_kernel_execution_is_interrupted_and_dropped(tmp_path: Path) -> None:
    worker = await KernelWorker.start(
        tmp_path,
        {},
        2**30,
        262144,
        30.0,
    )
    output_started = asyncio.Event()
    raw_output: list[str] = []

    def capture(raw: str) -> None:
        raw_output.append(raw)
        if "tick" in raw:
            output_started.set()

    task = asyncio.create_task(
        worker.run_code(
            "import time\nprint('tick', flush=True)\nwhile True:\n print('working\\r', end='', flush=True)\n time.sleep(0.05)",
            capture,
        )
    )
    try:
        await asyncio.wait_for(output_started.wait(), timeout=10)
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)

        visible = filter_tool_output("".join(raw_output))
        assert time.monotonic() - started < 3
        assert "tick" in visible
        assert "KeyboardInterrupt" in visible
        assert worker.alive is False
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.shutdown()
