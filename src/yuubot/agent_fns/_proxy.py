"""Module-level daemon RPC proxy for agent_fns packages.

Each agent_fns package creates one ``_DaemonProxy`` at import time.  The proxy
captures ``daemon_base_url``, ``token``, and service-needed state fields from
``SESSION_STATE`` once, then reuses them for all calls in the session.
"""

from __future__ import annotations

from typing import Any

import httpx


class AgentCallError(Exception):
    """Raised when a daemon /agent-fns call returns an error response."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


class _DaemonProxy:
    """Thin HTTP proxy for ``POST /agent-fns/{service}/{action}``."""

    __slots__ = ("_daemon_url", "_token", "_base")

    def __init__(self) -> None:
        raw: dict[str, Any] = {}
        try:
            from yuuagents.kernel import get_session_state

            s = get_session_state()
            raw = s.as_dict()
        except Exception:
            pass
        self._daemon_url: str = str(raw.get("daemon_base_url") or "")
        self._token: str = str(raw.get("token") or "")
        # Fields services need that _enrich_payload doesn't supply from token
        self._base: dict[str, Any] = {
            k: raw[k]
            for k in ("workspace_root", "recorder_base_url")
            if raw.get(k)
        }

    async def call(self, service: str, action: str, **kwargs: Any) -> Any:
        """POST to ``/agent-fns/{service}/{action}`` and return the JSON result."""
        payload: dict[str, Any] = {**self._base}
        payload.update({k: v for k, v in kwargs.items() if v is not None})
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{self._daemon_url}/agent-fns/{service}/{action}",
                json=payload,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:
                data = {}
            raise AgentCallError(
                code=str(data.get("code", "error")),
                message=str(data.get("message", r.text[:200])),
            )
        return r.json()
