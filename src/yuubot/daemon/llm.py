"""LLM command executor for the RFC2 yuuagents skeleton."""

from __future__ import annotations

import attrs
from loguru import logger

from yuubot.auth import bot_kind_for_message
from yuubot.characters import CHARACTER_REGISTRY, get_character
from yuubot.commands.tree import CommandRequest
from yuubot.config import Config
from yuubot.core.types import InboundMessage
from yuubot.daemon.agent_runner import AgentRunner
from yuubot.daemon.conversation import Conversation, ConversationManager

_BOT_KIND_DEFAULT_AGENT: dict[str, str] = {
    "master": "maid",
    "group": "yuu",
}


def _max_seen_db_id(message: InboundMessage) -> int:
    ids = [message.db_id, *(extra.db_id for extra in message.extra_messages)]
    return max((row_id for row_id in ids if row_id > 0), default=0)


@attrs.define
class LLMExecutor:
    conv_mgr: ConversationManager
    agent_runner: AgentRunner
    config: Config

    async def __call__(self, request: CommandRequest) -> str | None:
        text = request.remaining
        if text.startswith("continue"):
            after = text[len("continue"):]
            if not after or after[0].isspace():
                text = after.lstrip()

        message = request.message
        bot_kind = bot_kind_for_message(message, self.config.bot.master)
        session = self.conv_mgr.get(message.ctx_id)
        agent_name = self._resolve_agent_name(
            bot_kind=bot_kind,
            session=session,
        )
        if agent_name not in CHARACTER_REGISTRY:
            return f"未知 Agent: {agent_name}"

        character = get_character(agent_name)
        if not character.supports_bot_kind(bot_kind):
            return f"Agent {agent_name!r} 仅支持 {character.bot_kind} 场景"

        if session is None or session.agent_name != agent_name:
            session = self.conv_mgr.create(message.ctx_id, agent_name, user_id=message.sender.user_id, bot_kind=bot_kind)
        current_row_id = _max_seen_db_id(message)
        if current_row_id:
            if session.start_row_id == 0:
                session.start_row_id = current_row_id
            session.latest_ctx_row_id = max(session.latest_ctx_row_id, current_row_id)

        self.conv_mgr.set_running(message.ctx_id, agent_name)
        try:
            text_override = None if request.entry in {"master", "@"} else text
            runtime_session = await self.agent_runner.run_conversation(
                message,
                agent_name=agent_name,
                bot_kind=bot_kind,
                session=session,
                text_override=text_override,
            )
            if runtime_session is not None:
                self.conv_mgr.update_session(
                    session,
                    runtime_session,
                    max_context_tokens=character.spec.max_context_tokens,
                )
                self.conv_mgr.mark_delivered(session, current_row_id)
            return None
        except Exception:
            logger.exception("RFC2 LLM execution failed: ctx={} agent={}", message.ctx_id, agent_name)
            return "Agent 运行失败，已记录日志。"
        finally:
            self.conv_mgr.set_idle(message.ctx_id, agent_name)

    def _resolve_agent_name(
        self,
        *,
        bot_kind: str,
        session: Conversation | None,
    ) -> str:
        if session is not None:
            return session.agent_name
        return _BOT_KIND_DEFAULT_AGENT.get(bot_kind, "yuu")
