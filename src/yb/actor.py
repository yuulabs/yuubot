"""Actor context helpers for the handwritten yb facade."""

from __future__ import annotations

from yb import _context


def current() -> _context.ActorContext:
    return _context.actor_context()


def context() -> dict[str, str]:
    return current().as_dict()


def actor_id() -> str:
    return current().actor_id


def agent_name() -> str:
    return current().agent_name


def session_id() -> str:
    return current().session_id


def mailbox_id() -> str:
    return current().mailbox_id
