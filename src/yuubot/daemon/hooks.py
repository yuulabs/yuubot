from __future__ import annotations

import json
import uuid as _uuid
from typing import Any

import aiosqlite
from attrs import define, field


@define
class ContextHook:
    """Inject yuubot context into tool calls.

    Before execute_python: injects ``_token`` for kernel auth (used by local_api.py).
    Before bash: injects ``cwd`` set to the agent's workspace_root.
    """

    _agent_ctx: dict[str, dict[str, Any]] = field(factory=dict, init=False)

    def bind_agent(self, agent_id: str, ctx: dict[str, Any]) -> None:
        self._agent_ctx[agent_id] = ctx

    def unbind_agent(self, agent_id: str) -> None:
        self._agent_ctx.pop(agent_id, None)

    async def before(
        self, agent_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        ctx = self._agent_ctx.get(agent_id)
        if ctx is None:
            return tool_name, arguments
        if tool_name == "execute_python":
            arguments["_token"] = ctx.get("token", "")
        elif tool_name == "bash":
            arguments["cwd"] = ctx.get("workspace_root", ".")
        return tool_name, arguments

    async def after(
        self, agent_id: str, tool_name: str, result: Any
    ) -> Any:
        return result


@define
class ScheduleHook:
    """Pre-generate job_id for create_cron; persist job_id → ctx mapping to SQLite.

    The mapping lives in the same SQLite DB as ScheduleProvider's cron_jobs table,
    so it survives daemon restarts.  When a cron job fires, the ScheduleProvider
    sends a ``ScheduleTriggerMessage`` carrying the ``job_id``; the Actor calls
    ``lookup_ctx(job_id)`` to recover the target ctx_id and character.
    """

    db_path: str = ":memory:"
    _agent_ctx: dict[str, dict[str, Any]] = field(factory=dict, init=False)
    _cache: dict[str, dict[str, Any]] = field(factory=dict, init=False)
    _init_done: bool = field(default=False, init=False)

    def bind_agent(self, agent_id: str, ctx: dict[str, Any]) -> None:
        self._agent_ctx[agent_id] = ctx

    def unbind_agent(self, agent_id: str) -> None:
        self._agent_ctx.pop(agent_id, None)

    async def lookup_ctx(self, job_id: str) -> dict[str, Any] | None:
        """Look up schedule context by job_id.  Falls back to SQLite on cache miss."""
        if not job_id:
            return None
        # Fast path: in-memory cache
        cached = self._cache.get(job_id)
        if cached is not None:
            return cached
        # SQLite fallback (survives restarts)
        try:
            await self._ensure_db()
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT context_json FROM schedule_meta WHERE job_id = ?", (job_id,)
                ) as cur:
                    row = await cur.fetchone()
            if row is not None:
                ctx = json.loads(row["context_json"])
                self._cache[job_id] = ctx
                return ctx
        except Exception:
            pass
        return None

    async def before(
        self, agent_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        if tool_name != "create_cron":
            return tool_name, arguments

        job_id = str(arguments.get("job_id") or "") or _uuid.uuid4().hex[:8]
        arguments["job_id"] = job_id

        # Persist job_id → ctx mapping to SQLite (same DB as schedule cron_jobs)
        ctx = self._agent_ctx.get(agent_id, {})
        if ctx:
            try:
                await self._ensure_db()
                context_json = json.dumps({
                    "ctx_id": ctx.get("ctx_id", 0),
                    "character_name": ctx.get("character_name", ""),
                    "chat_type": ctx.get("chat_type", ""),
                    "reply_target": ctx.get("reply_target", ""),
                    "bot_kind": ctx.get("bot_kind", ""),
                })
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(
                        "INSERT OR REPLACE INTO schedule_meta (job_id, context_json) VALUES (?, ?)",
                        (job_id, context_json),
                    )
                    await db.commit()
            except Exception:
                pass

        return tool_name, arguments

    async def after(
        self, agent_id: str, tool_name: str, result: Any
    ) -> Any:
        return result

    async def _ensure_db(self) -> None:
        if self._init_done:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS schedule_meta ("
                "  job_id TEXT PRIMARY KEY,"
                "  context_json TEXT NOT NULL"
                ")"
            )
            await db.commit()
        self._init_done = True
