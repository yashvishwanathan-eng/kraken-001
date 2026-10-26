"""WebSocket connection manager for /ws/events.

Each dashboard holds one open connection. When something changes, the API calls broadcast()
and every connected dashboard gets the message at once: no polling. Each connection carries
a filter so a user only receives incidents from their delegated cameras.

Two kinds of message go out:
- an incident object (has "camera_id"): only users who can see that camera receive it;
- a notice with a "kind" key ("alert_all", "roll_call"): every logged-in dashboard receives it.

Escalation runs on timer threads, not in the web server's event loop, so it uses
publish_from_thread(), which hands the message to the event loop safely.
"""

import asyncio
import logging
from collections.abc import Callable

from fastapi import WebSocket

log = logging.getLogger("safetyai.ws")

MessageFilter = Callable[[dict], bool]


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, MessageFilter] = {}
        self._users: dict[WebSocket, int] = {}  # which user each dashboard belongs to ("on the software")
        self.loop: asyncio.AbstractEventLoop | None = None  # set when the API starts

    def publish_from_thread(self, message: dict) -> None:
        """Broadcast from a non-async thread (escalation timers, call dispatcher)."""
        if self.loop is None or self.loop.is_closed():
            log.debug("no event loop yet; message not pushed: %s", message.get("kind") or message.get("id"))
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)

    async def connect(self, websocket: WebSocket, allow: MessageFilter, user_id: int | None = None) -> None:
        await websocket.accept()
        self._clients[websocket] = allow
        if user_id is not None:
            self._users[websocket] = user_id

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.pop(websocket, None)
        self._users.pop(websocket, None)

    def online_user_ids(self) -> set[int]:
        """Users with at least one dashboard open right now. The call ladder gives them 5 s on screen first."""
        return set(self._users.values())

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
