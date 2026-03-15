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
from yuubot.commands.tree import CommandRequest
from yuubot.core.types import InboundMessage
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


def _should_auto_continue_rollover(message: InboundMessage, history: list) -> bool:
    """Allow at most one automatic rollover continuation without a final reply."""
    if _has_final_response(history):
        return False
    return int(message.raw_event.get("_rollover_auto_count", 0) or 0) < 1


async def _send_reply(message: InboundMessage, text: str, config: Config) -> None:
    """Send a text reply back to the source context."""
    from yuubot.core.models import Message, TextSegment
    from yuubot.core.onebot import build_send_msg

    segments: Message = [TextSegment(text=text)]
    target_id = message.group_id if message.chat_type == "group" else message.sender.user_id
    body = build_send_msg(message.chat_type, target_id, segments)
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
    - Delegating to agent_runner.run_conversation()
    - Post-turn history update and rollover (_finish_turn)
    """

    conv_mgr: ConversationManager
    agent_runner: AgentRunner
    config: Config
    role_mgr: RoleManager

    async def __call__(self, request: CommandRequest) -> str | None:
        # 1. Parse remaining text
        is_continue = False
        implicit_auto_resume = request.entry == "@"
        text = request.remaining
        if request.remaining.startswith("continue"):
            after = request.remaining[len("continue"):]
            if not after or after[0] in (" ", "\t", "\n"):
                is_continue = True
                text = after.lstrip()

        agent_name, text = _parse_agent_tag(text)

        message = request.message
        ctx_id = message.ctx_id
        user_id = message.sender.user_id
        msg_type = message.chat_type

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
                    return "请先使用 /close 关闭当前会话再切换 Agent"
                # Private: close old session and switch
                self.conv_mgr.close(ctx_id)
                await _send_reply(message, f"已切换到 Agent: {agent_name}", self.config)
                session = None
        elif self.conv_mgr.is_auto(ctx_id):
            # Auto mode, no active session:
            # - implicit bare-text/@bot continuation resumes current agent
            # - explicit /yllm or /yllm#agent honours the requested agent
            cur_agent = self.conv_mgr.current_agent(ctx_id)
            if cur_agent and (implicit_auto_resume or is_continue):
                agent_name = cur_agent

        # 4. Validate agent
        if not self._agent_exists(agent_name):
            return f"未知 Agent: {agent_name}"

        # 5. Agent-level permission check
        scope = str(message.group_id or "global")
        role = await self.role_mgr.get(user_id, scope)
        required_role = self.config.agent_min_role(agent_name)
        if role < required_role:
            return f"权限不足: Agent {agent_name!r} 需要 {required_role.name} 权限"

        # Create a new session if needed (no active session, or switched agent)
        if session is None or session.agent_name != agent_name:
            session = self.conv_mgr.create(ctx_id, agent_name, user_id=user_id)

        self.conv_mgr.set_running(ctx_id, agent_name)
        try:
            # 7. Run agent
            handoff_text = session.summary_prompt if session is not None and not session.history else ""
            runtime_session = await self.agent_runner.run_conversation(
                message,
                agent_name=agent_name,
                user_role=role.name,
                session=session,
                handoff_text=handoff_text,
                text_override=text,
            )
            if runtime_session is None:
                return None
            if session is not None and handoff_text:
                session.summary_prompt = ""

            # 8. Update session and handle rollover
            await self._finish_turn(session, runtime_session, message)
            return None
        finally:
            self.conv_mgr.set_idle(ctx_id, agent_name)

    def _agent_exists(self, name: str) -> bool:
        from yuubot.characters import CHARACTER_REGISTRY
        return name in CHARACTER_REGISTRY

    async def _finish_turn(
        self, conv: Conversation, runtime_session: object, message: InboundMessage
    ) -> None:
        """Update conversation history and handle token-limit rollover."""
        history = list(getattr(runtime_session, "history", []))
        rolled = self.conv_mgr.update_session(conv, runtime_session)
        if not rolled:
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

        should_continue = not message.raw_event.get("_is_auto_continuation")
        task = compact_original_task(history)
        summary_prompt = build_summary_prompt(task, note, should_continue=should_continue)

        new_session = self.conv_mgr.create(ctx_id, agent_name, user_id=user_id)
        new_session.summary_prompt = summary_prompt
        logger.info("Session rolled over: ctx={} agent={} note_len={}", ctx_id, agent_name, len(note))

        if worth_curating:
            asyncio.create_task(self._run_curator(history, ctx_id, user_id))

        if _should_auto_continue_rollover(message, history):
            scope = str(message.group_id or "global")
            role = await self.role_mgr.get(message.sender.user_id, scope)
            cont_message = attrs.evolve(
                message,
                raw_event={
                    **message.raw_event,
                    "_is_auto_continuation": True,
                    "_rollover_auto_count": int(message.raw_event.get("_rollover_auto_count", 0) or 0) + 1,
                },
            )
            next_session = await self.agent_runner.run_conversation(
                cont_message,
                agent_name=agent_name,
                user_role=role.name,
                session=None,
                handoff_text=summary_prompt,
            )
            if next_session is None:
                return
            new_session.summary_prompt = ""
            await _send_reply(message, "（已压缩上下文，继续处理中...）", self.config)
            await self._finish_turn(new_session, next_session, cont_message)
        else:
            if note:
                await _send_reply(message, "（已压缩上下文，新会话已就绪，可继续对话）", self.config)

    async def _run_curator(self, history: list, ctx_id: int, user_id: int) -> None:
        try:
            await self.agent_runner.curate(history, ctx_id, user_id)
        except Exception:
            logger.exception("mem_curator failed for ctx={}", ctx_id)
