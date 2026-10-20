"""WebSocket connection manager for /ws/events.

Each dashboard holds one open connection. When something changes, the API calls broadcast()
and every connected dashboard gets the message at once: no polling. Each connection carries
a filter so a user only receives incidents from their delegated cameras.
"""

import logging
from collections.abc import Callable

from fastapi import WebSocket

log = logging.getLogger("safetyai.ws")

MessageFilter = Callable[[dict], bool]


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, MessageFilter] = {}

    async def connect(self, websocket: WebSocket, allow: MessageFilter) -> None:
        await websocket.accept()
        self._clients[websocket] = allow

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.pop(websocket, None)

    @property
    def count(self) -> int:
        return len(self._clients)

    async def broadcast(self, message: dict) -> int:
        """Send to every client whose filter allows it. Returns how many received it."""
        sent = 0
        for websocket, allow in list(self._clients.items()):
            if not allow(message):
                continue
            try:
                await websocket.send_json(message)
                sent += 1
            except Exception:  # client went away mid-send
                log.info("dropping closed websocket")
                self.disconnect(websocket)
        return sent


manager = ConnectionManager()
