"""Internal WS relay — broadcast events to connected daemons."""

import asyncio
import json

import websockets
from websockets.asyncio.server import Server, ServerConnection

from loguru import logger


class RelayServer:
    """Internal WS server. Daemon connects here to receive events."""

    def __init__(self) -> None:
        self._clients: set[ServerConnection] = set()
        self._server: Server | None = None

    async def start(self, host: str, port: int) -> None:
        self._server = await websockets.serve(self._handler, host, port)
        logger.info("Relay WS listening on %s:%d", host, port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handler(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        logger.info("Daemon connected (%d clients)", len(self._clients))
        try:
            async for _ in ws:
                pass  # daemon doesn't send us anything
        finally:
            self._clients.discard(ws)
            logger.info("Daemon disconnected (%d clients)", len(self._clients))

    async def broadcast(self, data: dict) -> None:
        """Send event JSON to all connected daemons."""
        if not self._clients:
            return
        payload = json.dumps(data, ensure_ascii=False)
        dead: list[ServerConnection] = []
        for ws in self._clients:
            try:
                await ws.send(payload)
            except websockets.ConnectionClosed:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
