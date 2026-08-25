from __future__ import annotations

import asyncio
import threading


class QueueProgressBroadcaster:

    def __init__(self, main_loop: asyncio.AbstractEventLoop) -> None:
        self._main_loop = main_loop
        self._lock = threading.Lock()
        self._connections: dict[str, asyncio.Queue] = {}

    def connect(self, username: str) -> asyncio.Queue:
        connection: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._connections[username] = connection
        return connection

    def disconnect(self, username: str) -> None:
        with self._lock:
            self._connections.pop(username, None)

    def push(self, username: str, message: dict) -> None:
        with self._lock:
            connection = self._connections.get(username)
        if connection is not None:
            self._main_loop.call_soon_threadsafe(connection.put_nowait, message)
