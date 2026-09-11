"""The one broadcaster.

There were three, and two of them were the same class written twice: a
registry of per-user connections plus a thread-safe push onto them. The
third only recorded what passed through. What actually differed was
policy, not structure — whether pushes are coalesced inside a short
window, whether they are enriched with the running token total, whether
they also reach the shared /ws/notifications channel — so policy is what
this takes as arguments.

Two subtleties that are not obvious from the outside and must survive
any future edit:

*Connections are load-bearing.* `JobQueue.wait_for` waits by connecting
here and awaiting a push (jobs/job_queue.py) — a broadcaster that
delivered nothing would hang every caller waiting for a job to finish.
"Quiet" here means reaching no user-facing surface (no token totals, no
websocket), never dropping the message.

*The flush is a scheduled job, and it gets cancelled.* The batching
window used to be a raw threading.Timer — a new OS thread per window per
user, which always fired and did nothing when there was no longer
anything to deliver. It is an InMemScheduler entry now: one thread for
all of them, and a window that becomes pointless is cancelled rather
than left to fire. The scheduler and its queue are created on first use,
so a broadcaster that never batches never starts a thread; and that
queue is private, never the platform's own, because JobQueue reports
every job step *to a broadcaster* — a flush job on the shared queue
would generate the status that schedules the next flush.
"""
from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from system import bus
from system.bus import UI_PROGRESS, Message
from jobs.job import CancelableJob
from system.logging_factory import LoggerFactory

if TYPE_CHECKING:
    from ai import AiService

logger = LoggerFactory.get_logger(__name__)

DEFAULT_BATCH_WINDOW_SECONDS = 0.10


class _FlushJob(CancelableJob):
    """One batching window, as the scheduler understands it."""

    def __init__(self, broadcaster: "Broadcaster", username: str) -> None:
        super().__init__(key=f"broadcaster-flush:{username}", username=username)
        self._broadcaster = broadcaster
        self._username = username

    def _prepare(self) -> tuple[int, list[CancelableJob]]:
        return 1, []

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        self._broadcaster.flush(self._username)


class Broadcaster:
    """Per-user fan-out onto asyncio queues, pushed from any thread.

    `batch_window_seconds` at 0 delivers each message as it arrives; above
    0, messages sharing a `key` inside that window are merged and sent
    once — right for a progress indicator, wrong for anything whose
    pieces all matter, so a stream of chunks must use an unbatched one.
    `ai_service`, when given, adds the running token total to every
    message. Every message is also published on the Bus as
    bus.UI_PROGRESS, so an interface that wants to mirror it
    subscribes there — this object holds no reference to one."""

    def __init__(
        self,
        ai_service: "AiService | None" = None,
        batch_window_seconds: float = 0.0,
    ) -> None:
        self._ai_service = ai_service
        self._batch_window_seconds = batch_window_seconds
        self._lock = threading.RLock()
        self._connections: dict[str, dict[asyncio.Queue, asyncio.AbstractEventLoop]] = {}
        self._pending: dict[str, dict[object, dict]] = {}
        self._scheduled: dict[str, _FlushJob] = {}
        self._last_by_key: dict[str, dict] = {}
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._scheduler = None

    # --- publishing what was delivered ------------------------------------

    def bind_loop(self) -> None:
        """Called from main.py's async lifespan, so this is always the
        main uvicorn loop. A flush runs on a job-worker thread with its
        own unrelated loop, and a listener that ends up writing to a
        socket needs this specific loop, not whichever one happens to be
        running at flush time. Captured here rather than in __init__
        because the test suite constructs broadcasters outside any
        running loop at all; a broadcaster that was never bound simply
        does not publish, which is what a test with no interface wants."""
        self._main_loop = asyncio.get_running_loop()

    # --- connections -------------------------------------------------------

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
            # Nothing left to deliver to: drop what was waiting for its
            # window and cancel the window itself, rather than waking a
            # job up to discover there is no one there.
            if not self._deliverable(username):
                self._pending.pop(username, None)
                self._cancel_scheduled(username)

    # --- pushing -----------------------------------------------------------

    def push(self, username: str, message: dict) -> None:
        key = message.get("key")
        with self._lock:
            if key is not None:
                self._last_by_key[key] = message
            if self._batch_window_seconds <= 0:
                self._deliver(username, [message])
                return
            bucket = self._pending.setdefault(username, {})
            entry = bucket.get(key)
            if entry is not None:
                entry.update(message)
                return
            bucket[key] = dict(message)
            if len(bucket) > 1:
                return
            self._schedule_flush(username)

    def flush(self, username: str) -> None:
        """Sends whatever this user's window collected. Public because the
        scheduled job calls it; harmless to call directly."""
        with self._lock:
            self._scheduled.pop(username, None)
            bucket = self._pending.pop(username, None)
            messages = list(bucket.values()) if bucket else []
        if messages:
            self._deliver(username, messages)

    def total_tokens(self) -> int | None:
        return self._ai_service.get_total_tokens() if self._ai_service is not None else None

    def _deliver(self, username: str, messages: list[dict]) -> None:
        with self._lock:
            connections = list(self._connections.get(username, {}).items())
        listeners = self._listeners()
        if not connections and not listeners:
            return
        tokens = self.total_tokens()
        for message in messages:
            enriched = message if tokens is None else {**message, "tokens": tokens}
            for connection, loop in connections:
                loop.call_soon_threadsafe(connection.put_nowait, enriched)
            if listeners:
                asyncio.run_coroutine_threadsafe(
                    bus.publish(Message(type=UI_PROGRESS, body=enriched, username=username)), self._main_loop,
                )

    def _listeners(self) -> list:
        """Who would receive a UI_PROGRESS right now — empty until
        this broadcaster has a loop to publish on, since without one
        nothing can reach them anyway."""
        return bus.handlers_for(UI_PROGRESS) if self._main_loop is not None else []

    def _deliverable(self, username: str) -> bool:
        return bool(self._connections.get(username)) or bool(self._listeners())

    # --- the batching window, as a scheduled job ---------------------------

    def _schedule_flush(self, username: str) -> None:
        from datetime import datetime, timedelta, timezone

        job = _FlushJob(self, username)
        self._scheduled[username] = job
        self._ensure_scheduler().submit(
            job, timestamp=datetime.now(timezone.utc) + timedelta(seconds=self._batch_window_seconds)
        )

    def _cancel_scheduled(self, username: str) -> None:
        job = self._scheduled.pop(username, None)
        if job is not None and self._scheduler is not None:
            self._scheduler.cancel(job)

    def _ensure_scheduler(self):
        """Built on first use: a broadcaster that never batches never
        starts a thread. Its queue is private — see the module docstring
        on why a flush must not run on the platform's own."""
        if self._scheduler is None:
            from jobs.job_queue import JobQueue
            from scheduler.in_mem_scheduler import InMemScheduler

            self._scheduler = InMemScheduler(JobQueue(max_concurrent=1, broadcaster=Broadcaster()))
        return self._scheduler

    def close(self) -> None:
        """Drops every window still waiting. Nothing else to release: the
        connections belong to their own callers."""
        with self._lock:
            for username in list(self._scheduled):
                self._cancel_scheduled(username)
            self._pending.clear()

    # --- what was last said about each job ---------------------------------

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
