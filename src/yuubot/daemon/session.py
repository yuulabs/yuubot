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


@attrs.define
class SessionManager:
    """Manages active sessions keyed by (ctx_id, agent_name)."""

    ttl: float = 300.0
    max_tokens: int = 60000
    _sessions: dict[tuple[int, str], Session] = attrs.field(factory=dict)
    _has_active_agent_sessions: object = None  # callable() -> bool, set by daemon

    def get(self, ctx_id: int, agent_name: str | None = None) -> Session | None:
        """Return active session for ctx, optionally filtered by agent.

        If *agent_name* is None, return any active session for this ctx.
        Expired sessions are evicted on access.
        """
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

        # Find any active session for this ctx
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
        """Create a new session, replacing any existing one for this ctx."""
        # Remove any existing session for this ctx (regardless of agent)
        for key in [k for k in self._sessions if k[0] == ctx_id]:
            del self._sessions[key]

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
        if elapsed <= self.ttl:
            return False
        # Extend TTL if there are active agent sessions
        if self._has_active_agent_sessions is not None:
            try:
                if self._has_active_agent_sessions():
                    return False
            except Exception:
                pass
        return True
