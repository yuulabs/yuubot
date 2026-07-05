"""Actor lifecycle and conversation spawning."""

from .lifecycle import Actor, ActorConfig, build_conversation_context

__all__ = [
    "Actor",
    "ActorConfig",
    "build_conversation_context",
]
