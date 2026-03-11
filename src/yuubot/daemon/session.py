"""Session manager — maintains multi-turn conversation state per (ctx, agent)."""

import time

import attrs

from loguru import logger


@attrs.define
class Session:
    ctx_id: int
    agent_name: str
    history: list = attrs.field(factory=list)  # list[yuullm.Message]
    created_at: float = attrs.field(factory=time.monotonic)
    last_active: float = attrs.field(factory=time.monotonic)
    total_tokens: int = 0
    user_id: int = 0  # who started the session
    task_id: str = ""  # reused across continuations for trace continuity
    handoff_note: str = ""  # set when a rolled-over session carries context summary


@attrs.define
class SessionManager:
    """Manages active sessions keyed by (ctx_id, agent_name).

    Auto mode (private chat only):
    - Enabled per ctx_id via enable_auto()/disable_auto().
    - Sessions use a longer TTL (auto_ttl, default 1800s).
    - Multiple agents' sessions coexist; current_agent() tracks the active one.
    - /yllm#agent switches the active agent without killing other sessions.
    - When a session expires in auto mode, the next message auto-resumes with
      the same agent (no need for /yllm again).
    """

    ttl: float = 300.0
    auto_ttl: float = 1800.0
    max_tokens: int = 60000
    _sessions: dict[tuple[int, str], Session] = attrs.field(factory=dict)
    _is_ctx_active: object = None  # callable(int) -> bool, set by daemon
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

    def get(self, ctx_id: int, agent_name: str | None = None) -> Session | None:
        """Return active session for ctx, optionally filtered by agent.

        In auto mode with agent_name=None, returns the current agent's session.
        In normal mode with agent_name=None, returns any active session.
        Expired sessions are evicted on access.
        """
        if agent_name is None and ctx_id in self._auto_ctxs:
            agent_name = self._current_agent.get(ctx_id)

        if agent_name is not None:
            key = (ctx_id, agent_name)
            session = self._sessions.get(key)
            if session is None:
                return None
            if self._is_expired(session):
                del self._sessions[key]
                logger.info("Session expired: ctx={} agent={}", ctx_id, agent_name)
                return None
            return session

        # Normal mode: find any active session for this ctx
        for key, session in list(self._sessions.items()):
            if key[0] != ctx_id:
                continue
            if self._is_expired(session):
                del self._sessions[key]
                logger.info("Session expired: ctx={} agent={}", *key)
                continue
            return session
        return None

    def create(self, ctx_id: int, agent_name: str, user_id: int = 0) -> Session:
        """Create a new session for this ctx.

        In normal mode, replaces any existing session for the ctx.
        In auto mode, keeps other agents' sessions alive and updates current_agent.
        """
        if ctx_id not in self._auto_ctxs:
            for key in [k for k in self._sessions if k[0] == ctx_id]:
                del self._sessions[key]

        self._current_agent[ctx_id] = agent_name
        if ctx_id in self._auto_ctxs:
            self._sync_current_agent(ctx_id, agent_name)
        session = Session(ctx_id=ctx_id, agent_name=agent_name, user_id=user_id)
        self._sessions[(ctx_id, agent_name)] = session
        logger.info("Session created: ctx={} agent={}", ctx_id, agent_name)
        return session

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

    def touch(self, session: Session) -> None:
        """Refresh session TTL."""
        session.last_active = time.monotonic()

    def close(self, ctx_id: int) -> list[Session]:
        """Close all sessions for a ctx. Returns closed sessions."""
        keys = [k for k in self._sessions if k[0] == ctx_id]
        closed = [self._sessions.pop(key) for key in keys]
        self._current_agent.pop(ctx_id, None)
        if keys:
            logger.info("Session closed: ctx={}", ctx_id)
        return closed

    def collect_expired(self) -> list[Session]:
        """Evict all expired sessions and return them."""
        expired = []
        for key in list(self._sessions):
            session = self._sessions[key]
            if self._is_expired(session):
                del self._sessions[key]
                logger.info("Session expired: ctx={} agent={}", *key)
                expired.append(session)
        return expired

    def update_history(self, session: Session, history: list, tokens: int) -> bool:
        """Update session history and token count after agent run.

        *tokens* is the cumulative total from the agent. We compute the
        last-turn usage (delta) and compare against *max_tokens* so that
        sessions are only closed when a single LLM call becomes too large
        (i.e. the context window is filling up), not merely because the
        user has chatted for a while.

        Returns True if the session was closed due to reaching the token limit.
        """
        last_turn = tokens - session.total_tokens
        session.history = history
        session.total_tokens = tokens
        self.touch(session)
        if last_turn >= self.max_tokens:
            key = (session.ctx_id, session.agent_name)
            self._sessions.pop(key, None)
            logger.info(
                "Session closed (token limit): ctx=%s agent=%s last_turn=%d",
                session.ctx_id, session.agent_name, last_turn,
            )
            return True
        return False

    def _is_expired(self, session: Session) -> bool:
        elapsed = time.monotonic() - session.last_active
        effective_ttl = self.auto_ttl if session.ctx_id in self._auto_ctxs else self.ttl
        if elapsed <= effective_ttl:
            return False
        # Extend TTL if this ctx has a running agent flow
        if self._is_ctx_active is not None:
            try:
                if self._is_ctx_active(session.ctx_id):
                    return False
            except Exception:
                pass
        return True
