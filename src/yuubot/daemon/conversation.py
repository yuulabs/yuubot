"""Conversation manager — unified multi-turn conversation state per (ctx, agent).

Replaces the scattered session/flow/ping/auto-mode state with a single model.
A Conversation tracks state (idle/running/closed), pending messages received
while the agent is running, and all the session metadata previously in Session.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal

import attrs
from loguru import logger

from yuubot.core.types import InboundMessage

ConversationState = Literal["idle", "running", "closed"]

_CURATOR_MIN_TURNS = 3
_CURATOR_MIN_SECONDS = 60


def conversation_worth_curating(conv: Conversation) -> bool:
    """True if the conversation is substantial enough for the curator to bother."""
    duration = conv.last_active_at - conv.created_at
    turns = sum(1 for role, _ in conv.history if role == "assistant")
    return turns >= _CURATOR_MIN_TURNS and duration >= _CURATOR_MIN_SECONDS


@attrs.define
class Conversation:
    ctx_id: int
    agent_name: str
    mode: str = "normal"  # "normal" | "auto"
    state: ConversationState = "idle"
    history: list = attrs.field(factory=list)
    pending_messages: list[InboundMessage] = attrs.field(factory=list)
    started_by: int = 0  # user_id who started it
    last_active_at: float = attrs.field(factory=time.monotonic)
    task_id: str = ""
    total_tokens: int = 0
    created_at: float = attrs.field(factory=time.monotonic)
    summary_prompt: str = ""

    # Aliases for backward compatibility with Session field names
    @property
    def user_id(self) -> int:
        return self.started_by

    @property
    def last_active(self) -> float:
        return self.last_active_at


@attrs.define
class ConversationManager:
    """Manages active conversations keyed by (ctx_id, agent_name).

    Auto mode (private chat only):
    - Enabled per ctx_id via enable_auto()/disable_auto().
    - Conversations use a longer TTL (auto_ttl, default 1800s).
    - Multiple agents' conversations coexist; current_agent() tracks the active one.
    - /yllm#agent switches the active agent without killing other conversations.
    - When a conversation expires in auto mode, the next message auto-resumes with
      the same agent (no need for /yllm again).
    """

    ttl: float = 300.0
    auto_ttl: float = 1800.0
    max_tokens: int = 60000
    _conversations: dict[tuple[int, str], Conversation] = attrs.field(factory=dict)
    _is_ctx_active: Callable[[int], bool] | None = None  # set by daemon
    _auto_ctxs: set[int] = attrs.field(factory=set)
    _current_agent: dict[int, str] = attrs.field(factory=dict)  # ctx_id → agent_name

    # ── Auto mode ──────────────────────────────────────────────────────────────

    async def load_auto(self) -> None:
        """Restore auto mode state from DB on startup."""
        from yuubot.core.models import AutoModeSetting

        records = await AutoModeSetting.all()
        for r in records:
            self._auto_ctxs.add(r.ctx_id)
            if r.current_agent:
                self._current_agent[r.ctx_id] = r.current_agent
        if records:
            logger.info("Loaded auto mode for {} ctx(s)", len(records))

    async def enable_auto(self, ctx_id: int) -> None:
        from yuubot.core.models import AutoModeSetting

        self._auto_ctxs.add(ctx_id)
        await AutoModeSetting.update_or_create(
            defaults={"current_agent": self._current_agent.get(ctx_id, "")},
            ctx_id=ctx_id,
        )
        logger.info("Auto mode enabled: ctx={}", ctx_id)

    async def disable_auto(self, ctx_id: int) -> None:
        from yuubot.core.models import AutoModeSetting

        self._auto_ctxs.discard(ctx_id)
        self._current_agent.pop(ctx_id, None)
        await AutoModeSetting.filter(ctx_id=ctx_id).delete()
        logger.info("Auto mode disabled: ctx={}", ctx_id)

    def is_auto(self, ctx_id: int) -> bool:
        return ctx_id in self._auto_ctxs

    def current_agent(self, ctx_id: int) -> str | None:
        """Return the currently active agent for this ctx (auto mode only)."""
        return self._current_agent.get(ctx_id)

    # ── Core CRUD ──────────────────────────────────────────────────────────────

    def get(self, ctx_id: int, agent_name: str | None = None) -> Conversation | None:
        """Return active conversation for ctx, optionally filtered by agent.

        In auto mode with agent_name=None, returns the current agent's conversation.
        In normal mode with agent_name=None, returns any active conversation.
        Expired conversations are evicted on access.
        """
        if agent_name is None and ctx_id in self._auto_ctxs:
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

    def create(self, ctx_id: int, agent_name: str, user_id: int = 0) -> Conversation:
        """Create a new conversation for this ctx.

        In normal mode, replaces any existing conversation for the ctx.
        In auto mode, keeps other agents' conversations alive and updates current_agent.
        """
        if ctx_id not in self._auto_ctxs:
            for key in [k for k in self._conversations if k[0] == ctx_id]:
                del self._conversations[key]

        self._current_agent[ctx_id] = agent_name
        if ctx_id in self._auto_ctxs:
            self._sync_current_agent(ctx_id, agent_name)
        conv = Conversation(ctx_id=ctx_id, agent_name=agent_name, started_by=user_id)
        self._conversations[(ctx_id, agent_name)] = conv
        logger.info("Conversation created: ctx={} agent={}", ctx_id, agent_name)
        return conv

    def _sync_current_agent(self, ctx_id: int, agent_name: str) -> None:
        """Fire-and-forget DB update for current_agent in auto mode."""
        import asyncio
        from yuubot.core.models import AutoModeSetting

        async def _update():
            try:
                await AutoModeSetting.filter(ctx_id=ctx_id).update(current_agent=agent_name)
            except Exception:
                logger.warning("Failed to sync current_agent for ctx={}", ctx_id)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_update())
        except RuntimeError:
            pass

    def touch(self, conv: Conversation) -> None:
        """Refresh conversation TTL."""
        conv.last_active_at = time.monotonic()

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

    def update_history(self, conv: Conversation, history: list, tokens: int) -> bool:
        """Update conversation history and token count after agent run.

        *tokens* is the cumulative total from the agent. We compute the
        last-turn usage (delta) and compare against *max_tokens* so that
        conversations are only closed when a single LLM call becomes too large
        (i.e. the context window is filling up), not merely because the
        user has chatted for a while.

        Returns True if the conversation was closed due to reaching the token limit.
        """
        last_turn = tokens - conv.total_tokens
        conv.history = history
        conv.total_tokens = tokens
        self.touch(conv)
        if last_turn >= self.max_tokens:
            key = (conv.ctx_id, conv.agent_name)
            self._conversations.pop(key, None)
            logger.info(
                "Conversation closed (token limit): ctx=%s agent=%s last_turn=%d",
                conv.ctx_id, conv.agent_name, last_turn,
            )
            return True
        return False

    # ── Pending message support (replaces Ping mechanism) ──────────────────────

    def enqueue_pending(self, ctx_id: int, message: InboundMessage) -> None:
        """Add a message to pending while conversation is running."""
        conv = self.get(ctx_id)
        if conv and conv.state == "running":
            conv.pending_messages.append(message)

    def drain_pending(self, ctx_id: int) -> list[InboundMessage]:
        """Take all pending messages after agent turn ends."""
        conv = self.get(ctx_id)
        if conv:
            msgs = conv.pending_messages
            conv.pending_messages = []
            return msgs
        return []

    def set_running(self, ctx_id: int, agent_name: str | None = None) -> None:
        """Mark conversation as running (bypasses expiry check)."""
        conv = self._get_raw(ctx_id, agent_name)
        if conv:
            conv.state = "running"

    def set_idle(self, ctx_id: int, agent_name: str | None = None) -> None:
        """Mark conversation as idle after agent turn."""
        conv = self._get_raw(ctx_id, agent_name)
        if conv:
            conv.state = "idle"

    def _get_raw(self, ctx_id: int, agent_name: str | None = None) -> Conversation | None:
        """Get conversation without expiry check."""
        if agent_name is None and ctx_id in self._auto_ctxs:
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
        elapsed = time.monotonic() - conv.last_active_at
        effective_ttl = self.auto_ttl if conv.ctx_id in self._auto_ctxs else self.ttl
        if elapsed <= effective_ttl:
            return False
        # Extend TTL if this ctx has a running agent flow (legacy check)
        if self._is_ctx_active is not None:
            try:
                if self._is_ctx_active(conv.ctx_id):
                    return False
            except Exception:
                pass
        return True
