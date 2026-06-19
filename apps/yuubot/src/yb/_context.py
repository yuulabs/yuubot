"""Actor-local context access for the handwritten yb facade."""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from yuubot.core.facade.context import FACADE_CONTEXT_MODULE


@dataclass(frozen=True)
class ActorContext:
    actor_id: str
    agent_name: str
    session_id: str
    mailbox_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "actor_id": self.actor_id,
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "mailbox_id": self.mailbox_id,
        }


@dataclass(frozen=True)
class BridgeContext:
    host: str
    port: int
    token: str
    timeout_s: float


def actor_context() -> ActorContext:
    module = _context_module()
    return ActorContext(
        actor_id=str(module.ACTOR_ID),
        agent_name=str(module.AGENT_NAME),
        session_id=str(module.SESSION_ID),
        mailbox_id=str(module.MAILBOX_ID),
    )


def bridge_context() -> BridgeContext:
    module = _context_module()
    return BridgeContext(
        host=str(module.HOST),
        port=int(module.PORT),
        token=str(module.TOKEN),
        timeout_s=float(module.TIMEOUT_S),
    )


def _context_module():
    try:
        return importlib.import_module(FACADE_CONTEXT_MODULE)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "yb facade is only available inside an actor Python session"
        ) from exc
