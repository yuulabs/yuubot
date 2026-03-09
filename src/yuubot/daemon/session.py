"""Session manager — maintains multi-turn conversation state per (ctx, agent)."""

import logging
import time

import attrs

log = logging.getLogger(__name__)


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
    _has_active_agent_sessions: object = None  # callable() -> bool, set by daemon
    _auto_ctxs: set[int] = attrs.field(factory=set)
    _current_agent: dict[int, str] = attrs.field(factory=dict)  # ctx_id → agent_name

    # ── Auto mode ──────────────────────────────────────────────────────────────

    def enable_auto(self, ctx_id: int) -> None:
        self._auto_ctxs.add(ctx_id)
        log.info("Auto mode enabled: ctx=%s", ctx_id)

    def disable_auto(self, ctx_id: int) -> None:
        self._auto_ctxs.discard(ctx_id)
        self._current_agent.pop(ctx_id, None)
        log.info("Auto mode disabled: ctx=%s", ctx_id)

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
                log.info("Session expired: ctx=%s agent=%s", ctx_id, agent_name)
                return None
            return session

        # Normal mode: find any active session for this ctx
        for key, session in list(self._sessions.items()):
            if key[0] != ctx_id:
                continue
            if self._is_expired(session):
                del self._sessions[key]
                log.info("Session expired: ctx=%s agent=%s", *key)
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
        session = Session(ctx_id=ctx_id, agent_name=agent_name, user_id=user_id)
        self._sessions[(ctx_id, agent_name)] = session
        log.info("Session created: ctx=%s agent=%s", ctx_id, agent_name)
        return session

    def touch(self, session: Session) -> None:
        """Refresh session TTL."""
        session.last_active = time.monotonic()

    def close(self, ctx_id: int) -> bool:
        """Close all sessions for a ctx. Returns True if any were closed."""
        keys = [k for k in self._sessions if k[0] == ctx_id]
        for key in keys:
            del self._sessions[key]
        self._current_agent.pop(ctx_id, None)
        if keys:
            log.info("Session closed: ctx=%s", ctx_id)
        return bool(keys)

    def update_history(self, session: Session, history: list, tokens: int) -> None:
        """Update session history and token count after agent run.

        *tokens* is the cumulative total from the agent. We compute the
        last-turn usage (delta) and compare against *max_tokens* so that
        sessions are only closed when a single LLM call becomes too large
        (i.e. the context window is filling up), not merely because the
        user has chatted for a while.
        """
        last_turn = tokens - session.total_tokens
        session.history = history
        session.total_tokens = tokens
        self.touch(session)
        if last_turn >= self.max_tokens:
            key = (session.ctx_id, session.agent_name)
            del self._sessions[key]
            log.info(
                "Session closed (token limit): ctx=%s agent=%s last_turn=%d",
                session.ctx_id, session.agent_name, last_turn,
            )

    def _is_expired(self, session: Session) -> bool:
        elapsed = time.monotonic() - session.last_active
        effective_ttl = self.auto_ttl if session.ctx_id in self._auto_ctxs else self.ttl
        if elapsed <= effective_ttl:
            return False
        # Extend TTL if there are active agent sessions
        if self._has_active_agent_sessions is not None:
            try:
                if self._has_active_agent_sessions():
                    return False
            except Exception:
                pass
        return True
