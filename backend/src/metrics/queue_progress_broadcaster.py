from __future__ import annotations

import asyncio
import threading


class QueueProgressBroadcaster:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: dict[str, tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = {}

    def connect(self, username: str) -> asyncio.Queue:
        connection: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        with self._lock:
            self._connections[username] = (connection, loop)
        return connection

    def disconnect(self, username: str, connection: asyncio.Queue) -> None:
        with self._lock:
            entry = self._connections.get(username)
            if entry is not None and entry[0] is connection:
                del self._connections[username]

    def push(self, username: str, message: dict) -> None:
        # Per Claude Code: qui stai identificando dove mandare la notifica per username. e che succede se hai piu' richieste dallo stesso username?
        # dovesti identificare la key per richiesta http, non per username.
        with self._lock:
            entry = self._connections.get(username)
        if entry is not None:
            connection, loop = entry
            loop.call_soon_threadsafe(connection.put_nowait, message)
