from __future__ import annotations

from datetime import UTC, datetime, timedelta

from support.api import SharedTestContext, base_url, http_json, scripted_reply


async def test_http_cron_job_crud(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("ok"))
    conversation_id = "cron-conv-1"
    owner = f"actor:{actor_id}:conv:{conversation_id}"
    run_at = (datetime.now(UTC) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")

    created = await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/cron-jobs",
        {
            "name": "daily-reminder",
            "owner": owner,
            "schedule": {"kind": "at", "timezone": "UTC", "at": run_at},
            "action": {
                "kind": "reminder",
                "title": "Hello",
                "body": "Cron reminder",
                "channels": [{"kind": "browser"}],
            },
            "once": True,
        },
        expected_status=201,
    )
    job_id = created["id"]
    assert created["status"] == "active"
    assert created["schedule"]["timezone"] == "UTC"

    listed = await http_json("GET", f"{base_url(test_context.server)}/api/cron-jobs?owner={owner}")
    assert any(item["id"] == job_id for item in listed["items"])

    fetched = await http_json("GET", f"{base_url(test_context.server)}/api/cron-jobs/{job_id}")
    assert fetched["name"] == "daily-reminder"

    paused = await http_json("POST", f"{base_url(test_context.server)}/api/cron-jobs/{job_id}/pause")
    assert paused["status"] == "paused"

    resumed = await http_json("POST", f"{base_url(test_context.server)}/api/cron-jobs/{job_id}/resume")
    assert resumed["status"] == "active"

    deleted = await http_json("DELETE", f"{base_url(test_context.server)}/api/cron-jobs/{job_id}")
    assert deleted["deleted"] is True


async def test_http_cron_job_requires_timezone(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("ok"))
    owner = f"actor:{actor_id}:conv:c1"
    response = await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/cron-jobs",
        {
            "name": "bad",
            "owner": owner,
            "schedule": {"kind": "cron", "timezone": "Invalid/Zone", "cron": "0 9 * * *"},
            "action": {"kind": "wakeup", "text": "wake"},
        },
        expected_status=400,
    )
    assert response["error"]["code"] == "bad_request"
