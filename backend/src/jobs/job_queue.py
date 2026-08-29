from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass
from typing import ClassVar, TYPE_CHECKING

# Instructions for Claude Code: DO NOT TOUCH THIS FILE

from logging_factory import LoggerFactory
from .job import CancelableJob, DependentJob

if TYPE_CHECKING:
    from metrics.queue_progress_broadcaster import QueueProgressBroadcaster

logger = LoggerFactory.get_logger(__name__)

class JobQueue(object):
    """Generic job orchestrator: submit, queue, run on a dedicated thread
    pool. One deque, shared by every worker — a non-background job jumps
    to the head, a background one joins the tail; workers always pop the
    head, so interactive jobs preempt background ones without needing a
    separate queue or thread pool for each."""

    @dataclass(frozen=True)
    class STATUS:
        value: str
        ready: ClassVar["JobQueue.STATUS"]
        running: ClassVar["JobQueue.STATUS"]
        exited: ClassVar["JobQueue.STATUS"]

    STATUS.ready = STATUS("ready")
    STATUS.running = STATUS("running")
    STATUS.exited = STATUS("exited")

    def __init__(self, max_concurrent: int, broadcaster: QueueProgressBroadcaster) -> None:
        self._broadcaster = broadcaster
        self._lock = threading.RLock()
        self._not_empty = threading.Condition(self._lock)
        self._deque: deque[DependentJob] = deque()
        self._waiters: dict[DependentJob, list[threading.Event]] = {}

        for i in range(max_concurrent):
            thread = threading.Thread(target=self.__worker_loop, name=f"job-worker-{i}", daemon=True)
            thread.start()

    def __forget(self, job: DependentJob) -> STATUS:
        ready = [p for p in job.parents if p is not job and p._dependency_resolved(job)]
        with self._lock:
            events = self._waiters.pop(job, [])
        for waiter in ready:
            status = self._submit(waiter, waiter.is_background)
            self._broadcast_status(waiter, status)
        for event in events:
            event.set()

        return self.STATUS.exited

    def _submit(self, job: DependentJob, low_priority : bool) -> STATUS:
        with self._not_empty:
            if low_priority:
                self._deque.append(job)
            else:
                self._deque.appendleft(job)
            self._not_empty.notify()
        return self.STATUS.ready

    def __dequeue(self) -> DependentJob:
        with self._not_empty:
            while not self._deque:
                self._not_empty.wait()
            return self._deque.popleft()

    def _continue(self, job: DependentJob) -> None:
        """Called when a worker would begin to run step."""
        return

    def _has_priority_work_waiting(self) -> bool:
        with self._not_empty:
            return bool(self._deque) and not self._deque[0].is_background

    def _should_requeue(self, job: DependentJob) -> bool:
        return job.is_requeued() or (
            not job.is_done() and (not job.is_background or self._has_priority_work_waiting())
        )

    def _broadcast_status(self, job: DependentJob, queue_status: JobQueue.STATUS) -> None:
        status = {
            "key": job.key,
            "job_status": job.status().value,
            "percentage": job.progress(),
            "result": job.result,
            "error": job.error(),
            "queue_status": queue_status.value,
        }
        self._broadcaster.push(job.username, status)

    def __execute_job_step(self, job: DependentJob, loop: asyncio.AbstractEventLoop) -> None:

        while not job.is_done():
            self._continue(job)
            self._broadcast_status(job, self.STATUS.running)

            try:
                loop.run_until_complete(job.run_next_step())
            except Exception as exc:
                logger.exception(f"Job {job} failed: {exc}")
                break

            if self._should_requeue(job):
                status = self._submit(job, job.is_requeued() or job.is_background)
                self._broadcast_status(job, status)
                return

        status = self.__forget(job)
        self._broadcast_status(job, status)


    def __worker_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            job = self.__dequeue()
            self.__execute_job_step(job, loop)

    def submit(self, job: DependentJob, parent: DependentJob | None = None) -> None:
        with self._lock:
            if not job.is_pending():
                if parent is not None and job._add_parent_job(parent) and parent._dependency_resolved(job):
                    status = self._submit(parent, parent.is_background)
                    self._broadcast_status(parent, status)
                return

        self._broadcast_status(job, self.STATUS.ready)
        dependencies = job.prepare(parent)

        for dependency in dependencies:
            self.submit(dependency, parent=job)

        if job._children_registered():
            status = self._submit(job, job.is_background)
            self._broadcast_status(job, status)

    def cancel(self, job: CancelableJob) -> None:
        for canceled_job in job.cancel():
            self._broadcast_status(canceled_job, self.STATUS.exited)


    async def wait_for(self, job: DependentJob) -> None:
        connection = self._broadcaster.connect(job.username)
        try:
            while not (job.is_done() or job.is_failed()):
                await connection.get()
        finally:
            self._broadcaster.disconnect(job.username, connection)
