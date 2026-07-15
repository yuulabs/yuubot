from __future__ import annotations

import pytest

import yb.tasks


@pytest.mark.asyncio
async def test_manual_submit_requires_ttl() -> None:
    with pytest.raises(ValueError, match="requires ttl_s"):
        await yb.tasks.submit("name", "true", "intro", delivery="manual")


@pytest.mark.asyncio
async def test_submit_rejects_ttl_over_one_hour() -> None:
    with pytest.raises(ValueError, match="<= 3600"):
        await yb.tasks.submit("name", "true", "intro", delivery="conversation", ttl_s=3601)


@pytest.mark.asyncio
async def test_submit_propagates_parent_task_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def request_json(
        method: str,
        url: str,
        params: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
        timeout_s: float = 30,
    ) -> dict[str, object]:
        del method, url, params, timeout_s
        captured.update(json or {})
        return {
            "id": "t-child",
            "name": "child",
            "status": "running",
            "parent_task_id": "t-parent",
            "root_task_id": "t-parent",
        }

    monkeypatch.setenv("YUUBOT_TASK_OWNER", "actor:amy:conv:subagent:t-parent")
    monkeypatch.setenv("YUUBOT_PARENT_TASK_ID", "t-parent")
    monkeypatch.setattr(yb.tasks, "request_json", request_json)

    task = await yb.tasks.submit("child", "true", "intro", delivery="conversation")

    assert captured["parent_task_id"] == "t-parent"
    assert task.parent_task_id == "t-parent"
    assert task.root_task_id == "t-parent"


@pytest.mark.asyncio
async def test_list_tasks_sends_tree_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    async def request_json(
        method: str,
        url: str,
        params: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
        timeout_s: float = 30,
    ) -> dict[str, object]:
        del method, url, json, timeout_s
        captured.update(params or {})
        return {"items": []}

    monkeypatch.setenv("YUUBOT_TASK_OWNER", "actor:amy:conv:c1")
    monkeypatch.setattr(yb.tasks, "request_json", request_json)

    await yb.tasks.list_tasks(parent_task_id="t-parent", root_task_id="t-root")

    assert captured["parent_task_id"] == "t-parent"
    assert captured["root_task_id"] == "t-root"
