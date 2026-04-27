"""Conversation manager — unified multi-turn conversation state per context."""

from __future__ import annotations

import time
from typing import Literal

import attrs
from loguru import logger
import yuullm

from yuubot.daemon.runtime_session import RuntimeSession

ConversationState = Literal["idle", "running", "closed"]

_CURATOR_MIN_TURNS = 3
_CURATOR_MIN_SECONDS = 60


def conversation_worth_curating(conv: Conversation) -> bool:
    """True if the conversation is substantial enough for the curator to bother."""
    duration = conv.last_active_at - conv.created_at
    turns = sum(1 for item in conv.history if item.role == "assistant")
    return turns >= _CURATOR_MIN_TURNS and duration >= _CURATOR_MIN_SECONDS


@attrs.define
class Conversation:
    ctx_id: int
    agent_name: str
    state: ConversationState = "idle"
    bot_kind: str = "group"
    started_by: int = 0  # user_id who started it
    last_active_at: float = attrs.field(factory=time.monotonic)
    total_tokens: int = 0
    created_at: float = attrs.field(factory=time.monotonic)
    summary_prompt: str = ""
    original_task: str = ""  # persists the very first user request across rollovers
    start_row_id: int = 0
    latest_ctx_row_id: int = 0
    delivered_row_id: int = 0
    session: RuntimeSession | None = None
    _history_snapshot: list[yuullm.Message] = attrs.field(factory=list)

    @property
    def history(self) -> list[yuullm.Message]:
        if self.session is None:
            return list(self._history_snapshot)
        return list(self.session.history)

    @history.setter
    def history(self, value: list) -> None:
        self._history_snapshot = list(value)

    @property
    def task_id(self) -> str:
        if self.session is None:
            return ""
        return self.session.task_id

@attrs.define
class ConversationManager:
    """Manages the active conversation for each context."""

    ttl: float = 300.0
    master_ttl: float = 3600.0
    max_tokens: int = 60000
    _conversations: dict[tuple[int, str], Conversation] = attrs.field(factory=dict)
    _current_agent: dict[int, str] = attrs.field(factory=dict)  # ctx_id → agent_name

    def current_agent(self, ctx_id: int) -> str | None:
        """Return the currently active agent for this ctx."""
        return self._current_agent.get(ctx_id)

    # ── Core CRUD ──────────────────────────────────────────────────────────────

    def get(self, ctx_id: int, agent_name: str | None = None) -> Conversation | None:
        """Return active conversation for ctx, optionally filtered by agent."""
        if agent_name is None:
            agent_name = self._current_agent.get(ctx_id)

        if agent_name is not None:
            key = (ctx_id, agent_name)
            conv = self._conversations.get(key)
            if conv is None:
                return None
            if self._is_expired(conv):
                del self._conversations[key]
                logger.info("Conversation expired: ctx={} agent={}", ctx_id, agent_name)
                return None
            return conv

        # Normal mode: find any active conversation for this ctx
        for key, conv in list(self._conversations.items()):
            if key[0] != ctx_id:
                continue
            if self._is_expired(conv):
                del self._conversations[key]
                logger.info("Conversation expired: ctx={} agent={}", *key)
                continue
            return conv
        return None

    def create(self, ctx_id: int, agent_name: str, user_id: int = 0, bot_kind: str = "group") -> Conversation:
        """Create a new conversation for this ctx."""
        for key in [k for k in self._conversations if k[0] == ctx_id]:
            del self._conversations[key]

        self._current_agent[ctx_id] = agent_name
        conv = Conversation(ctx_id=ctx_id, agent_name=agent_name, bot_kind=bot_kind, started_by=user_id)
        self._conversations[(ctx_id, agent_name)] = conv
        logger.info("Conversation created: ctx={} agent={} bot_kind={}", ctx_id, agent_name, bot_kind)
        return conv

    def touch(self, conv: Conversation) -> None:
        """Refresh conversation TTL."""
        conv.last_active_at = time.monotonic()

    def observe_message(self, ctx_id: int, row_id: int) -> None:
        """Record the latest persisted QQ message visible in this ctx."""
        if row_id <= 0:
            return
        for key, conv in self._conversations.items():
            if key[0] != ctx_id:
                continue
            if row_id > conv.latest_ctx_row_id:
                conv.latest_ctx_row_id = row_id
            if conv.start_row_id == 0:
                conv.start_row_id = row_id

    def mark_delivered(self, conv: Conversation, row_id: int) -> None:
        """Mark QQ messages up to ``row_id`` as already delivered to the LLM."""
        if row_id <= 0:
            return
        if conv.start_row_id == 0:
            conv.start_row_id = row_id
        if row_id > conv.delivered_row_id:
            conv.delivered_row_id = row_id
        if row_id > conv.latest_ctx_row_id:
            conv.latest_ctx_row_id = row_id

    def close(self, ctx_id: int) -> list[Conversation]:
        """Close all conversations for a ctx. Returns closed conversations."""
        keys = [k for k in self._conversations if k[0] == ctx_id]
        closed = [self._conversations.pop(key) for key in keys]
        self._current_agent.pop(ctx_id, None)
        if keys:
            logger.info("Conversation closed: ctx={}", ctx_id)
        return closed

    def collect_expired(self) -> list[Conversation]:
        """Evict all expired conversations and return them."""
        expired = []
        for key in list(self._conversations):
            conv = self._conversations[key]
            if self._is_expired(conv):
                del self._conversations[key]
                logger.info("Conversation expired: ctx={} agent={}", *key)
                expired.append(conv)
        return expired

    def update_session(self, conv: Conversation, session: RuntimeSession, *, max_context_tokens: int | None = None) -> bool:
        """Update conversation state after one runtime session step.

        Rollover keys off the latest API call's reported context size estimate:
        input tokens (cached + non-cached) plus output tokens. We fall back to the
        legacy cumulative ``total_tokens`` delta only when structured usage is not
        available yet.

        Returns True if the conversation was closed due to reaching the token limit.
        """
        tokens = session.total_tokens
        last_usage = session.last_usage
        if last_usage is not None:
            last_turn = (
                (last_usage.input_tokens or 0)
                + (last_usage.cache_read_tokens or 0)
                + (last_usage.cache_write_tokens or 0)
                + (last_usage.output_tokens or 0)
            )
        else:
            last_turn = tokens - conv.total_tokens
        conv.session = session
        conv._history_snapshot = list(session.history)
        conv.total_tokens = tokens
        self.touch(conv)
        limit = max_context_tokens if max_context_tokens is not None else self.max_tokens
        if last_turn >= limit:
            key = (conv.ctx_id, conv.agent_name)
            self._conversations.pop(key, None)
            logger.info(
                "Conversation closed (token limit): ctx=%s agent=%s last_turn=%d",
                conv.ctx_id, conv.agent_name, last_turn,
            )
            return True
        return False

    def set_running(self, ctx_id: int, agent_name: str | None = None) -> None:
        """Mark conversation as running (bypasses expiry check)."""
        conv = self._get_raw(ctx_id, agent_name)
        if conv:
            prev = conv.state
            conv.state = "running"
            logger.debug("Conversation state: ctx={} agent={} {} → running", ctx_id, agent_name or conv.agent_name, prev)

    def set_idle(self, ctx_id: int, agent_name: str | None = None) -> None:
        """Mark conversation as idle after agent turn."""
        conv = self._get_raw(ctx_id, agent_name)
        if conv:
            prev = conv.state
            conv.state = "idle"
            logger.debug("Conversation state: ctx={} agent={} {} → idle", ctx_id, agent_name or conv.agent_name, prev)

    def _get_raw(self, ctx_id: int, agent_name: str | None = None) -> Conversation | None:
        """Get conversation without expiry check."""
        if agent_name is None:
            agent_name = self._current_agent.get(ctx_id)

        if agent_name is not None:
            return self._conversations.get((ctx_id, agent_name))

        for key, conv in self._conversations.items():
            if key[0] == ctx_id:
                return conv
        return None

    # ── Internal ───────────────────────────────────────────────────────────────

    def _is_expired(self, conv: Conversation) -> bool:
        # Running conversations never expire
        if conv.state == "running":
            return False
        ttl = self.master_ttl if conv.bot_kind == "master" else self.ttl
        elapsed = time.monotonic() - conv.last_active_at
        return elapsed > ttl
