"""LLMExecutor — all LLM/agent invocation logic in one place.

Replaces the scattered _exec_llm + dispatcher session logic with a single
callable that owns session selection, agent dispatch, and turn management.
"""

import asyncio
import re
from typing import TYPE_CHECKING

import attrs
import httpx
from loguru import logger

if TYPE_CHECKING:
    from yuubot.daemon.agent_runner import AgentRunner

from yuubot.commands.roles import RoleManager
from yuubot.commands.tree import Command, MatchResult
from yuubot.config import Config
from yuubot.daemon.conversation import Conversation, ConversationManager, conversation_worth_curating
from yuubot.daemon.summarizer import build_summary_prompt, compact_original_task

_AGENT_TAG_RE = re.compile(r"^#(\w+)\s*")


def _parse_agent_tag(text: str) -> tuple[str, str]:
    """Parse optional ``#agent_name`` prefix. Returns (agent_name, rest)."""
    m = _AGENT_TAG_RE.match(text.strip())
    if m:
        return m.group(1), text.strip()[m.end():]
    return "main", text


def _has_final_response(history: list) -> bool:
    """True when the turn already produced user-visible assistant text."""
    for msg in reversed(history):
        if not (isinstance(msg, tuple) and len(msg) == 2):
            continue
        role, items = msg
        if role != "assistant" or not isinstance(items, list):
            continue
        text = "".join(item for item in items if isinstance(item, str)).strip()
        if text:
            return True
    return False


def _should_auto_continue_rollover(event: dict, history: list) -> bool:
    """Allow at most one automatic rollover continuation without a final reply."""
    if _has_final_response(history):
        return False
    return int(event.get("_rollover_auto_count", 0) or 0) < 1


async def _send_reply(event: dict, text: str, config: Config) -> None:
    """Send a text reply back to the source context."""
    from yuubot.core.models import Message, TextSegment
    from yuubot.core.onebot import build_send_msg

    segments: Message = [TextSegment(text=text)]
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

    conv_mgr: ConversationManager
    agent_runner: AgentRunner
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
        session = self.conv_mgr.get(ctx_id)

        # 3. Decide which session/agent to use
        if session is not None:
            if is_continue or agent_name == session.agent_name:
                # Continue existing session — keep agent
                self.conv_mgr.touch(session)
                agent_name = session.agent_name
            else:
                # Different agent requested
                if msg_type == "group":
                    await _send_reply(event, "请先使用 /close 关闭当前会话再切换 Agent", self.config)
                    return
                # Private: close old session and switch
                self.conv_mgr.close(ctx_id)
                await _send_reply(event, f"已切换到 Agent: {agent_name}", self.config)
                session = None
        elif self.conv_mgr.is_auto(ctx_id):
            # Auto mode, no active session — auto-resume or honour explicit agent
            cur_agent = self.conv_mgr.current_agent(ctx_id)
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
            session = self.conv_mgr.create(ctx_id, agent_name, user_id=user_id)

        # 6. Build synthetic MatchResult for agent_runner (carries remaining text)
        synth_match = MatchResult(
            command=Command(prefix="llm", executor=self, interactive=True),
            remaining=text,
            entry="",
        )

        # 7. Run agent
        handoff_text = session.summary_prompt if session is not None and not session.history else ""
        history, tokens, task_id = await self.agent_runner.run(
            synth_match, event,
            agent_name=agent_name,
            user_role=role.name,
            session=session,
            handoff_text=handoff_text,
        )
        if session is not None and handoff_text:
            session.summary_prompt = ""
        session.task_id = task_id

        # 8. Update session and handle rollover
        await self._finish_turn(session, history, tokens, event)

    def _agent_exists(self, name: str) -> bool:
        from yuubot.characters import CHARACTER_REGISTRY
        return name in CHARACTER_REGISTRY

    async def _finish_turn(
        self, conv: Conversation, history: list, tokens: int, event: dict
    ) -> None:
        """Update conversation history and handle token-limit rollover."""
        pending_messages = self.conv_mgr.drain_pending(conv.ctx_id)
        rolled = self.conv_mgr.update_history(conv, history, tokens)
        if not rolled:
            if pending_messages:
                await self._continue_with_pending(conv, event, pending_messages)
            return

        ctx_id = conv.ctx_id
        agent_name = conv.agent_name
        user_id = conv.started_by
        worth_curating = conversation_worth_curating(conv)

        try:
            note = await self.agent_runner.summarize(history, agent_name)
        except Exception:
            logger.exception("Failed to summarize session for ctx={}", ctx_id)
            note = ""

        should_continue = not event.get("_is_auto_continuation")
        task = compact_original_task(history)
        summary_prompt = build_summary_prompt(task, note, should_continue=should_continue)

        new_session = self.conv_mgr.create(ctx_id, agent_name, user_id=user_id)
        new_session.summary_prompt = summary_prompt
        logger.info("Session rolled over: ctx={} agent={} note_len={}", ctx_id, agent_name, len(note))

        if worth_curating:
            asyncio.create_task(self._run_curator(history, ctx_id, user_id))

        if _should_auto_continue_rollover(event, history):
            scope = str(event.get("group_id", "global"))
            role = await self.role_mgr.get(event["user_id"], scope)
            synth_match = MatchResult(
                command=Command(prefix="llm", executor=self, interactive=True),
                remaining="",
                entry="rollover",
            )
            cont_event = dict(event)
            cont_event["_is_auto_continuation"] = True
            cont_event["_rollover_auto_count"] = int(event.get("_rollover_auto_count", 0) or 0) + 1
            h2, t2, tid2 = await self.agent_runner.run(
                synth_match, cont_event,
                agent_name=agent_name,
                user_role=role.name,
                session=None,
                pending_messages=pending_messages,
                handoff_text=summary_prompt,
            )
            new_session.task_id = tid2
            new_session.summary_prompt = ""
            await _send_reply(event, "（已压缩上下文，继续处理中...）", self.config)
            await self._finish_turn(new_session, h2, t2, cont_event)
        else:
            if note:
                await _send_reply(event, "（已压缩上下文，新会话已就绪，可继续对话）", self.config)

    async def _continue_with_pending(
        self,
        conv: Conversation,
        event: dict,
        pending_messages: list,
    ) -> None:
        """Run another turn with all messages that arrived while the agent was busy."""
        next_event = dict(pending_messages[0].raw_event)
        scope = str(next_event.get("group_id", "global"))
        role = await self.role_mgr.get(next_event["user_id"], scope)
        synth_match = MatchResult(
            command=Command(prefix="llm", executor=self, interactive=True),
            remaining="",
            entry="pending",
        )
        if len(pending_messages) > 1:
            next_event["_extra_events"] = [msg.raw_event for msg in pending_messages[1:]]
        history, tokens, task_id = await self.agent_runner.run(
            synth_match,
            next_event,
            agent_name=conv.agent_name,
            user_role=role.name,
            session=conv,
            pending_messages=pending_messages[1:],
            handoff_text=conv.summary_prompt if not conv.history else "",
        )
        if conv.summary_prompt and history:
            conv.summary_prompt = ""
        conv.task_id = task_id
        await self._finish_turn(conv, history, tokens, next_event)

    async def _run_curator(self, history: list, ctx_id: int, user_id: int) -> None:
        try:
            await self.agent_runner.curate(history, ctx_id, user_id)
        except Exception:
            logger.exception("mem_curator failed for ctx={}", ctx_id)
