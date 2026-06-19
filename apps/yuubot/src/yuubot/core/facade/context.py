"""Actor-local facade context module rendering."""

from __future__ import annotations

FACADE_CONTEXT_MODULE = "yuubot_facade_context"


def render_context_module(
    *,
    actor_id: str,
    agent_name: str,
    session_id: str,
    mailbox_id: str,
    host: str,
    port: int,
    token: str,
) -> str:
    return f'''"""Actor-local facade runtime context."""

from __future__ import annotations

HOST = {host!r}
PORT = {port!r}
TOKEN = {token!r}
TIMEOUT_S = 10.0
ACTOR_ID = {actor_id!r}
AGENT_NAME = {agent_name!r}
SESSION_ID = {session_id!r}
MAILBOX_ID = {mailbox_id!r}
'''
