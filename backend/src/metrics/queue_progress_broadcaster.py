from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.ai_service import AiService


class QueueProgressBroadcaster:

    def __init__(self, ai_service: "AiService") -> None:
        self._ai_service = ai_service
        self._lock = threading.Lock()
        self._connections: dict[str, dict[asyncio.Queue, asyncio.AbstractEventLoop]] = {}

    def connect(self, username: str) -> asyncio.Queue:
        connection: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        with self._lock:
            self._connections.setdefault(username, {})[connection] = loop
        return connection

    def disconnect(self, username: str, connection: asyncio.Queue) -> None:
        with self._lock:
            connections = self._connections.get(username)
            if connections is not None:
                connections.pop(connection, None)
                if not connections:
                    del self._connections[username]

    def push(self, username: str, message: dict) -> None:
        with self._lock:
            connections = list(self._connections.get(username, {}).items())
        if not connections:
            return
        enriched = {**message, "tokens": self._ai_service.get_total_tokens()}
        for connection, loop in connections:
            loop.call_soon_threadsafe(connection.put_nowait, enriched)
