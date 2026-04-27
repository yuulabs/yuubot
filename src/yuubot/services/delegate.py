"""Master-only delegate service for child-agent orchestration."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

import attrs

from yuubot.characters import CHARACTER_REGISTRY, get_character
from yuubot.core.models import TextSegment
from yuubot.core.types import InboundMessage, Sender
from yuubot.services.base import AccessDenied, YuubotServiceError


class _DelegateRunner(Protocol):
    async def run_conversation(self, message: InboundMessage, **kwargs: Any) -> Any: ...


def _is_master(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("bot_kind", "")).lower() == "master"


def _int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, int | float | str | bytes | bytearray) and not isinstance(value, bool):
            return int(value)
    except (TypeError, ValueError):
        return default
    return default


@attrs.define
class DelegateTask:
    id: str
    agent: str
    prompt: str
    status: str = "running"
    result: Any = None
    error: str = ""
    created_at: float = attrs.field(factory=time.time)
    done_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "done_at": self.done_at,
        }


@attrs.define
class DelegateService:
    runner: _DelegateRunner | None = None
    _tasks: dict[str, DelegateTask] = attrs.field(factory=dict)

    async def delegate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not _is_master(payload):
            raise AccessDenied("delegate is master-only")
        if self.runner is None:
            raise YuubotServiceError("delegate runner is not configured")
        target = str(payload.get("agent", payload.get("target", "")) or "")
        prompt = str(payload.get("task", payload.get("prompt", "")) or "").strip()
        if target not in CHARACTER_REGISTRY:
            raise YuubotServiceError(f"unknown delegate agent: {target}")
        if not prompt:
            raise YuubotServiceError("delegate task is empty")
        parent_name = str(payload.get("character_name", payload.get("agent_name", "yuu")) or "yuu")
        parent = get_character(parent_name)
        policy = parent.spec.delegate_policy
        allowed = set(policy.allowed_agents if policy is not None else ())
        if target not in allowed and "*" not in allowed:
            raise AccessDenied(f"delegate target is not allowed: {target}")

        task = DelegateTask(id=uuid.uuid4().hex[:12], agent=target, prompt=prompt)
        self._tasks[task.id] = task
        inbound = InboundMessage(
            message_id=0,
            ctx_id=_int(payload.get("ctx_id")),
            chat_type="private",
            group_id=0,
            self_id=_int(payload.get("bot_id")),
            sender=Sender(user_id=_int(payload.get("user_id")), nickname="Master"),
            segments=[TextSegment(text=prompt)],
            timestamp=int(time.time()),
            raw_message=prompt,
            raw_event={"delegate_task_id": task.id},
        )
        try:
            timeout_s = payload.get("timeout_s")
            coro = self.runner.run_conversation(
                inbound,
                agent_name=target,
                bot_kind="master",
                text_override=prompt,
                handoff_text=f"你是被 {parent_name} 委派的子 agent。请完成任务后给出结果。",
                send_reply=False,
            )
            session = await asyncio.wait_for(coro, timeout=float(timeout_s)) if timeout_s else await coro
            task.status = "finished"
            task.result = session.final_text if session is not None else ""
            task.done_at = time.time()
            return task.to_dict()
        except Exception as exc:
            task.status = "error"
            task.error = f"{type(exc).__name__}: {exc}"
            task.done_at = time.time()
            raise

    async def task_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("task_id", payload.get("id", "")) or "")
        if task_id:
            task = self._tasks.get(task_id)
            return task.to_dict() if task is not None else {"status": "not_found", "id": task_id}
        return {"tasks": [task.to_dict() for task in self._tasks.values()]}

    async def task_cancel(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("task_id", payload.get("id", "")) or "")
        task = self._tasks.get(task_id)
        if task is None:
            return {"status": "not_found", "id": task_id}
        if task.status == "running":
            task.status = "cancelled"
            task.done_at = time.time()
        return task.to_dict()
