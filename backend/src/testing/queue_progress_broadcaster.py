from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai.ai_service import AiService

DEFAULT_BATCH_WINDOW_SECONDS = 0.10


class QueueProgressBroadcaster:

    def __init__(self, ai_service: "AiService", batch_window_seconds: float = DEFAULT_BATCH_WINDOW_SECONDS) -> None:
        self._ai_service = ai_service
        self._batch_window_seconds = batch_window_seconds
        self._lock = threading.Lock()
        self._connections: dict[str, dict[asyncio.Queue, asyncio.AbstractEventLoop]] = {}
        self._pending: dict[str, dict] = {}

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
            pending = self._pending.get(username)
            if pending is not None:
                pending.update(message)
                return
            self._pending[username] = dict(message)
            timer = threading.Timer(self._batch_window_seconds, self.__flush, args=(username,))
            timer.daemon = True
            timer.start()

    def __flush(self, username: str) -> None:
        with self._lock:
            message = self._pending.pop(username, None)
            connections = list(self._connections.get(username, {}).items())
        if message is None or not connections:
            return
        enriched = {**message, "tokens": self._ai_service.get_total_tokens()}
        for connection, loop in connections:
            loop.call_soon_threadsafe(connection.put_nowait, enriched)
