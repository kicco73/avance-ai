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
        self._pending: dict[str, dict[object, dict]] = {}

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
            bucket = self._pending.setdefault(username, {})
            key = message.get("key")
            entry = bucket.get(key)
            if entry is not None:
                entry.update(message)
                return
            bucket[key] = dict(message)
            if len(bucket) > 1:
                return
            timer = threading.Timer(self._batch_window_seconds, self.__flush, args=(username,))
            timer.daemon = True
            timer.start()

    def __flush(self, username: str) -> None:
        with self._lock:
            bucket = self._pending.pop(username, None)
            connections = list(self._connections.get(username, {}).items())
        if not bucket or not connections:
            return
        tokens = self._ai_service.get_total_tokens()
        for message in bucket.values():
            enriched = {**message, "tokens": tokens}
            for connection, loop in connections:
                loop.call_soon_threadsafe(connection.put_nowait, enriched)
