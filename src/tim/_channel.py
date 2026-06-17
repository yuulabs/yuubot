"""Channel — send messages directly to an integration channel."""

from __future__ import annotations

import msgspec

from yuubot.core.facade.protocol import FacadeRpcRequest, ImSendPayload
from yb import _client, _context


class Channel:
    """A communication channel that can receive outbound messages.

    ``path`` is the channel identifier passed through to the integration's
    ``response()`` method. The integration uses it to route the message to
    the correct channel/room/thread.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    async def send(self, text: str) -> dict[str, object]:
        """Send a text message to this channel via the facade bridge."""
        payload = ImSendPayload(path=self.path, text=text)
        request = _im_send_request(payload)
        response = await _client.request(request)
        return response.result


def _im_send_request(payload: ImSendPayload) -> FacadeRpcRequest:
    actor = _context.actor_context()
    bridge = _context.bridge_context()
    return FacadeRpcRequest(
        token=bridge.token,
        kind="im_send",
        actor_id=actor.actor_id,
        agent_name=actor.agent_name,
        session_id=actor.session_id,
        mailbox_id=actor.mailbox_id,
        payload=msgspec.to_builtins(payload),
    )
