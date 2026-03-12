"""LLMExecutor — all LLM/agent invocation logic in one place.

Replaces the scattered _exec_llm + dispatcher session logic with a single
callable that owns session selection, agent dispatch, and turn management.
"""

import asyncio
import re

import attrs
import httpx
from loguru import logger

from yuubot.commands.roles import RoleManager
from yuubot.commands.tree import Command, MatchResult
from yuubot.config import Config
from yuubot.daemon.session import Session, SessionManager, session_worth_curating

_AGENT_TAG_RE = re.compile(r"^#(\w+)\s*")


def _parse_agent_tag(text: str) -> tuple[str, str]:
    """Parse optional ``#agent_name`` prefix. Returns (agent_name, rest)."""
    m = _AGENT_TAG_RE.match(text.strip())
    if m:
        return m.group(1), text.strip()[m.end():]
    return "main", text


async def _send_reply(event: dict, text: str, config: Config) -> None:
    """Send a text reply back to the source context."""
    from yuubot.core.models import TextSegment
    from yuubot.core.onebot import build_send_msg

    segments = [TextSegment(text=text)]
    msg_type = event.get("message_type", "private")
    target_id = event.get("group_id", 0) if msg_type == "group" else event.get("user_id", 0)
    body = build_send_msg(msg_type, target_id, segments)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{config.daemon.recorder_api}/send_msg", json=body)
    except Exception:
        logger.exception("Failed to send reply")


@attrs.define
class LLMExecutor:
    """Callable that handles the full LLM invocation lifecycle.

    Injected into the ``llm`` Command as its executor. Owns:
    - Parsing ``continue`` prefix and ``#agent_name`` tag from remaining text
    - Session selection (continue, switch, or new)
    - Agent-level permission check
    - Delegating to agent_runner.run()
    - Post-turn history update and rollover (_finish_turn)
    """

    session_mgr: SessionManager
    agent_runner: object  # AgentRunner (avoid circular import with type annotation)
    config: Config
    role_mgr: RoleManager

    async def __call__(self, remaining: str, event: dict, deps: object) -> None:
        # 1. Parse remaining text
        is_continue = False
        text = remaining
        if remaining.startswith("continue"):
            after = remaining[len("continue"):]
            if not after or after[0] in (" ", "\t", "\n"):
                is_continue = True
                text = after.lstrip()

        agent_name, text = _parse_agent_tag(text)

        ctx_id = event.get("ctx_id", 0)
        user_id = event.get("user_id", 0)
        msg_type = event.get("message_type", "private")

        # 2. Get current session
        session = self.session_mgr.get(ctx_id)

        # 3. Decide which session/agent to use
        if session is not None:
            if is_continue or agent_name == session.agent_name:
                # Continue existing session — keep agent
                self.session_mgr.touch(session)
                agent_name = session.agent_name
            else:
                # Different agent requested
                if msg_type == "group":
                    await _send_reply(event, "请先使用 /close 关闭当前会话再切换 Agent", self.config)
                    return
                # Private: close old session and switch
                self.session_mgr.close(ctx_id)
                await _send_reply(event, f"已切换到 Agent: {agent_name}", self.config)
                session = None
        elif self.session_mgr.is_auto(ctx_id):
            # Auto mode, no active session — auto-resume or honour explicit agent
            cur_agent = self.session_mgr.current_agent(ctx_id)
            if cur_agent and (is_continue or agent_name == "main"):
                agent_name = cur_agent

        # 4. Validate agent
        if not self._agent_exists(agent_name):
            await _send_reply(event, f"未知 Agent: {agent_name}", self.config)
            return

        # 5. Agent-level permission check
        scope = str(event.get("group_id", "global"))
        role = await self.role_mgr.get(user_id, scope)
        required_role = self.config.agent_min_role(agent_name)
        if role < required_role:
            await _send_reply(
                event,
                f"权限不足: Agent {agent_name!r} 需要 {required_role.name} 权限",
                self.config,
            )
            return

        # Create a new session if needed (no active session, or switched agent)
        if session is None or session.agent_name != agent_name:
            session = self.session_mgr.create(ctx_id, agent_name, user_id=user_id)

        # 6. Build synthetic MatchResult for agent_runner (carries remaining text)
        synth_match = MatchResult(
            command=Command(prefix="llm", executor=self, interactive=True),
            remaining=text,
            entry="",
        )

        # 7. Run agent
        history, tokens, task_id = await self.agent_runner.run(
            synth_match, event,
            agent_name=agent_name,
            user_role=role.name,
            session=session,
        )
        session.task_id = task_id

        # 8. Update session and handle rollover
        await self._finish_turn(session, history, tokens, event)

    def _agent_exists(self, name: str) -> bool:
        from yuubot.characters import CHARACTER_REGISTRY
        return name in CHARACTER_REGISTRY

    async def _finish_turn(
        self, session: Session, history: list, tokens: int, event: dict
    ) -> None:
        """Update session history and handle token-limit rollover."""
        rolled = self.session_mgr.update_history(session, history, tokens)
        if not rolled:
            return

        ctx_id = session.ctx_id
        agent_name = session.agent_name
        user_id = session.user_id
        worth_curating = session_worth_curating(session)

        await _send_reply(event, "（上下文已满，正在压缩摘要，稍后继续...）", self.config)

        try:
            note = await self.agent_runner.summarize(history, agent_name)
        except Exception:
            logger.exception("Failed to summarize session for ctx={}", ctx_id)
            note = ""

        new_session = self.session_mgr.create(ctx_id, agent_name, user_id=user_id)
        new_session.handoff_note = note
        logger.info("Session rolled over: ctx={} agent={} note_len={}", ctx_id, agent_name, len(note))

        if worth_curating:
            asyncio.create_task(self._run_curator(history, ctx_id, user_id))

        if not event.get("_is_auto_continuation"):
            await _send_reply(event, "（已压缩上下文，自动继续...）", self.config)
            scope = str(event.get("group_id", "global"))
            role = await self.role_mgr.get(event["user_id"], scope)
            synth_match = MatchResult(
                command=Command(prefix="llm", executor=self, interactive=True),
                remaining="请继续之前未完成的工作。",
                entry="",
            )
            cont_event = dict(event)
            cont_event["_is_auto_continuation"] = True
            h2, t2, tid2 = await self.agent_runner.run(
                synth_match, cont_event,
                agent_name=agent_name,
                user_role=role.name,
                session=new_session,
            )
            new_session.task_id = tid2
            await self._finish_turn(new_session, h2, t2, cont_event)
        else:
            if note:
                await _send_reply(event, "（已压缩上下文，新会话已就绪，可继续对话）", self.config)

    async def _run_curator(self, history: list, ctx_id: int, user_id: int) -> None:
        try:
            await self.agent_runner.curate(history, ctx_id, user_id)
        except Exception:
            logger.exception("mem_curator failed for ctx={}", ctx_id)
