from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import TYPE_CHECKING

from .job import Job

if TYPE_CHECKING:
    from metrics.queue_progress_broadcaster import QueueProgressBroadcaster

logger = logging.getLogger(__name__)


class JobQueue(object):
    """Generic job orchestrator: submit, queue, run on a dedicated thread
    pool. One deque, shared by every worker — a non-background job jumps
    to the head, a background one joins the tail; workers always pop the
    head, so interactive jobs preempt background ones without needing a
    separate queue or thread pool for each."""

    def __init__(self, max_concurrent: int, broadcaster: "QueueProgressBroadcaster") -> None:
        self._broadcaster = broadcaster
        self._broadcast_routing: dict[Job, tuple[str, str]] = {}
        self._lock = threading.RLock()
        self._not_empty = threading.Condition(self._lock)
        self._deque: deque[Job] = deque()
        self._dependents: dict[Job, list[Job]] = {}

        for i in range(max_concurrent):
            thread = threading.Thread(target=self._worker_loop, name=f"job-worker-{i}", daemon=True)
            thread.start()

    def _forget(self, job: Job) -> None:
        with self._lock:
            self._dependents.pop(job, None)
            ready = [
                waiter for waiter, deps in self._dependents.items()
                if job in deps and all(dep.is_done() or dep.is_failed() for dep in deps)
            ]
            for waiter in ready:
                del self._dependents[waiter]
        for waiter in ready:
            self._submit(waiter)

    def _submit(self, job: Job) -> None:
        with self._not_empty:
            if job.is_background:
                self._deque.append(job)
            else:
                self._deque.appendleft(job)
            self._not_empty.notify()

    def _dequeue(self) -> Job:
        with self._not_empty:
            while not self._deque:
                self._not_empty.wait()
            return self._deque.popleft()

    def _broadcast_status(self, job: Job, error: str | None = None) -> None:
        with self._lock:
            routing = self._broadcast_routing.get(job)
            if routing is None:
                return
            key, username = routing
            status = self.status_for(key, username) or {}
            if job.is_done() or error:
                del self._broadcast_routing[job]
        self._broadcaster.push(username, status)

    def _worker_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            job = self._dequeue()
            try:
                loop.run_until_complete(job.run_next_step())
                self._broadcast_status(job)
                if job.is_done():
                    self._forget(job)
                else:
                    self._submit(job)
            except Exception as exc:
                logger.exception(f"Job {job} failed.")
                self._broadcast_status(job, error=str(exc))
                self._forget(job)

    def submit(self, job: Job) -> None:
        dependencies = job.prepare()

        with self._lock:
            self._dependents[job] = dependencies

        for dependency in dependencies:
            self.submit(dependency)

        if not dependencies:
            self._submit(job)

    def submit_with_progress_feedback(self, job: Job, key: str, username: str) -> None:
        with self._lock:
            self._broadcast_routing[job] = (key, username)
        self.submit(job)

    def status_for(self, key: str, username: str) -> dict | None:
        with self._lock:
            for job, (routed_key, routed_username) in self._broadcast_routing.items():
                if routed_key == key and routed_username == username:
                    return {
                        "key": key,
                        "status": 'failed' if job.is_failed() else 'completed' if job.is_done() else 'running',
                        "percentage": job.progress(),
                        "result": job.result,
                    }
