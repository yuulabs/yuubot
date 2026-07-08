from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from yuubot import Yuubot

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from support.api import SharedTestContext, boot_app, running_server  # noqa: E402
from support.exec_py import exec_py_context  # noqa: F401,E402
from support.workspaces import prepare_session_workspaces, reset_workspace_files  # noqa: E402


@pytest.fixture(scope="session")
async def prepared_workspaces(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return await prepare_session_workspaces(tmp_path_factory.mktemp("yuubot-workspaces"))


@pytest.fixture(scope="session")
async def shared_server(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[object]:
    app = await boot_app(tmp_path_factory.mktemp("yuubot-shared") / "data")
    async with running_server(app) as server:
        yield server


@pytest.fixture
async def test_context(
    shared_server: object,
    prepared_workspaces: tuple[Path, Path],
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> AsyncIterator[SharedTestContext]:
    context = SharedTestContext(shared_server, tmp_path, request.node.name, prepared_workspaces)
    reset_workspace_files(context.workspace)
    try:
        yield context
    finally:
        await context.cleanup()
        reset_workspace_files(context.workspace)


@pytest.fixture(autouse=True)
async def track_created_apps(monkeypatch: pytest.MonkeyPatch) -> Any:
    apps: list[Yuubot] = []
    original_create = Yuubot.create

    async def create(
        cls: type[Yuubot],
        data_dir: str | Path,
        python_kernels: Any = None,
        resources: Any = None,
    ) -> Yuubot:
        del cls
        app = await original_create(data_dir, python_kernels=python_kernels, resources=resources)
        apps.append(app)
        return app

    monkeypatch.setattr(Yuubot, "create", classmethod(create))
    try:
        yield
    finally:
        for app in reversed(apps):
            await app.shutdown()
