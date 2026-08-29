from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio


class LastStatusBroadcaster:
    """Sits between JobQueue and the real QueueProgressBroadcaster (same
    connect/disconnect/push interface, so JobQueue never knows the
    difference): records the last message pushed for each job key, then
    forwards it unchanged. One broadcaster serves the live SSE stream;
    this one's own recorded state serves TestService.get_jobs_status() —
    a REST snapshot reads back exactly what SSE clients were already
    told, instead of separately re-deriving status from job.status()/the
    DB and risking the two disagreeing."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self._last_by_key: dict[str, dict] = {}

    def connect(self, username: str) -> "asyncio.Queue":
        return self._inner.connect(username)

    def disconnect(self, username: str, connection: "asyncio.Queue") -> None:
        self._inner.disconnect(username, connection)

    def push(self, username: str, message: dict) -> None:
        key = message.get("key")
        if key is not None:
            with self._lock:
                self._last_by_key[key] = message
        self._inner.push(username, message)

    def last_status(self, key: str) -> dict | None:
        with self._lock:
            return self._last_by_key.get(key)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._last_by_key.values())

    def forget(self, key: str) -> None:
        with self._lock:
            self._last_by_key.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._last_by_key.clear()
