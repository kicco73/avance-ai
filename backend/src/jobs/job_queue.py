from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import TYPE_CHECKING

# Instructions for Claude Code: DO NOT TOUCH THIS FILE

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

    def _broadcast_status(self, job: Job) -> None:
        status = {
            "key": job.key,
            "status": job.status(),
            "percentage": job.progress(),
            "result": job.result,
            "error": job.error(),
        }
        self._broadcaster.push(job.username, status)

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
                logger.exception(f"Job {job} failed: {exc}")
                self._broadcast_status(job)
                self._forget(job)


    def submit(self, job: Job) -> None:
        with self._lock:
            # Already prepared — some other waiter got to this exact job
            # object first (a dependency shared across two different
            # parents, e.g. the same session under both "sessions" and
            # "users" when root runs everything at once). is_pending() is
            # true only before prepare() has ever run, so this catches a
            # job that's still in flight AND one that already finished
            # (possibly before this second parent even got here) alike.
            if not job.is_pending():
                return

        self._broadcast_status(job)
        dependencies = job.prepare()

        with self._lock:
            self._dependents[job] = dependencies

        for dependency in dependencies:
            self.submit(dependency)

        with self._lock:
            # Every dependency may already be terminal by now — including
            # one that finished before this loop even started, which would
            # otherwise never get another chance to notify this waiter (its
            # own _forget() already ran, once, before this entry existed).
            ready = job in self._dependents and all(dep.is_done() or dep.is_failed() for dep in self._dependents[job])
            if ready:
                del self._dependents[job]
        if ready:
            self._submit(job)
