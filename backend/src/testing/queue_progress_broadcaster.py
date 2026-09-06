from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai import AiService
    from chat.ws_notifications import WsNotifications

DEFAULT_BATCH_WINDOW_SECONDS = 0.10


class QueueProgressBroadcaster:

    def __init__(self, ai_service: "AiService", batch_window_seconds: float = DEFAULT_BATCH_WINDOW_SECONDS) -> None:
        self._ai_service = ai_service
        self._batch_window_seconds = batch_window_seconds
        self._lock = threading.Lock()
        self._connections: dict[str, dict[asyncio.Queue, asyncio.AbstractEventLoop]] = {}
        self._pending: dict[str, dict[object, dict]] = {}
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._ws_notifications: "WsNotifications | None" = None

    def set_ws_notifications(self, ws_notifications: "WsNotifications") -> None:
        # Called from main.py's async lifespan, so this is always the main
        # uvicorn loop — every __flush() call below runs on a job-worker
        # thread with its own unrelated loop (see jobs/job_queue.py's
        # __worker_loop), so pushing onto the shared /ws/notifications
        # connection needs this specific loop handed in, not whichever one
        # happens to be running at flush time. Captured here rather than in
        # __init__ because the test suite constructs this broadcaster
        # outside any running loop at all.
        self._main_loop = asyncio.get_running_loop()
        self._ws_notifications = ws_notifications

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
        if not bucket or (not connections and self._ws_notifications is None):
            return
        tokens = self._ai_service.get_total_tokens()
        for message in bucket.values():
            enriched = {**message, "tokens": tokens}
            for connection, loop in connections:
                loop.call_soon_threadsafe(connection.put_nowait, enriched)
            if self._ws_notifications is not None and self._main_loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._ws_notifications.push(username, {"type": "test_update", **enriched}), self._main_loop,
                )
