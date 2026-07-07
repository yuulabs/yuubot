from __future__ import annotations

from pathlib import Path

import pytest

from yuubot.python.facade_imports import (
    ALL_FACADE_PACKAGES,
    facade_bootstrap_code,
    facade_bootstrap_module_source,
)
from yuubot.python.worker import KernelWorker
from yuubot.python.workspace import ensure_workspace_venv, prepare_kernel_workspace


def test_all_facade_packages_include_registered_integrations_and_runtime_facades() -> None:
    assert "yext.opencode" in ALL_FACADE_PACKAGES
    assert "yext.codex" in ALL_FACADE_PACKAGES
    assert "yext.github" in ALL_FACADE_PACKAGES
    assert "yext.web" in ALL_FACADE_PACKAGES
    assert "yb.tasks" in ALL_FACADE_PACKAGES
    assert "yb.mcps" in ALL_FACADE_PACKAGES
    assert "yb.skills" in ALL_FACADE_PACKAGES
    assert "yb.office.pdf" in ALL_FACADE_PACKAGES


def test_facade_bootstrap_code_imports_every_package() -> None:
    code = facade_bootstrap_code()
    for package in ALL_FACADE_PACKAGES:
        assert f"import {package}\n" in code


def test_prepare_kernel_workspace_writes_facade_bootstrap_module(tmp_path: Path) -> None:
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
