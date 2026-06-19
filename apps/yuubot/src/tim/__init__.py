"""tim — Test IM SDK for actor Python sessions.

Provides a simple ``Channel`` object that actors can use to send messages
directly to integration channels via the facade bridge RPC.  This bypasses
the actor mailbox entirely — messages go straight to the integration.

Usage::

    import tim

    channel = tim.Channel("group-1")
    await channel.send("Hello everyone!")
"""

from __future__ import annotations

from tim._channel import Channel as Channel

__all__ = ["Channel"]
