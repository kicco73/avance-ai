"""The one owner of the process's shared background work.

Every job the platform runs outside a test run — a session import, a
project upload, a session summary, an outgoing mail, a cross-project
wake-up, an actuator.defer — goes through this service: it holds the
shared JobQueue (worker pool) and the Scheduler (time-based hand-off
into that queue) as private members, and nothing else in the codebase
constructs or touches either. Consumers get four verbs — submit now,
schedule for later, cancel, wait — plus stream_progress for the
"run a job, watch it inline" endpoints. Built once in main.py's own
wiring and handed to whoever needs it, same as every other service.

A job that must run *later* is a jobs.Task, and schedule() hibernates
it in the Task table rather than holding it in memory (see
persisted_scheduler.py): a restart, a deploy, a crash change nothing
about when it runs.

Test runs are the deliberate exception: TestService owns its own
ThrottledJobQueue (see testing/test_service.py), a separate pool with
its own rate limits, so a batch of test replays can never starve an
interactive job here."""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from fastapi.responses import StreamingResponse

from jobs.job import CancelableJob, DependentJob
from jobs.job_queue import JobQueue
from jobs.task import Task
from logging_factory import LoggerFactory

from .persisted_scheduler import Hydrator, PersistedScheduler

if TYPE_CHECKING:
    from db import Db
    from testing.queue_progress_broadcaster import QueueProgressBroadcaster

logger = LoggerFactory.get_logger(__name__)


class JobService:
    """Two-phase, like every service that owns a thread here: constructed
    early (everything else is handed it), started last (main.py's own
    wiring calls start() once every task type has registered its
    hydrator and everything a running task may reach — the websocket
    adapter above all — exists). Until start() the scheduler's queue,
    the Task table, only ever *gains* rows: nothing is claimed, so a
    task can never run against a half-built process."""

    def __init__(
        self, max_concurrent: int, broadcaster: "QueueProgressBroadcaster", db: "Db", *,
        task_lease_seconds: float = 600.0,
    ) -> None:
        self._broadcaster = broadcaster
        self._queue = JobQueue(max_concurrent=max_concurrent, broadcaster=broadcaster)
        self._hydrators: dict[str, Hydrator] = {}
        self._scheduler = PersistedScheduler(self._queue, db, self._hydrators, lease_seconds=task_lease_seconds)
        self._started = False

    def register_task_type(self, task_type: str, hydrator: Hydrator) -> None:
        """Teaches the scheduler how to rebuild a hibernated Task row of
        `task_type` (a jobs.Task subclass's TYPE). Only before start():
        a row claimed with no hydrator for it is marked failed, and
        that must be impossible by construction, not by luck."""
        if self._started:
            raise RuntimeError(f"register_task_type('{task_type}') after start() — register every task type first.")
        if task_type in self._hydrators:
            raise ValueError(f"Task type '{task_type}' is already registered.")
        self._hydrators[task_type] = hydrator

    def start(self) -> None:
        """Starts claiming due tasks. Once, at the end of wiring."""
        if self._started:
            return
        self._started = True
        self._scheduler.start()

    def stop(self) -> None:
        """Stops claiming scheduled tasks (rows are untouched). For a
        clean shutdown, and for tests that start a service per test."""
        self._scheduler.stop()

    def submit(self, job: DependentJob, parent: DependentJob | None = None) -> None:
        """Runs `job` as soon as a worker is free (its own dependencies
        first — see JobQueue.submit). `parent`: the job waiting on this one."""
        self._queue.submit(job, parent)

    def schedule(self, task: Task, when: datetime) -> None:
        """Runs `task` no earlier than `when` (naive is read as UTC),
        restart or not: it is hibernated in the Task table right here
        and only rebuilt when due. A `when` already in the past runs as
        soon as the scheduler gets to it."""
        self._scheduler.submit(task, timestamp=when)

    def cancel(self, job: CancelableJob) -> None:
        """Drops `job` whether it is still waiting for its time, queued,
        or running (a running job stops at its next step)."""
        self._scheduler.cancel(job)

    async def wait_for(self, job: DependentJob) -> None:
        await self._queue.wait_for(job)

    def stream_progress(self, job: DependentJob) -> StreamingResponse:
        """Submits `job` (already carrying its own key/username, see
        Job.__init__) and streams its progress back as SSE on this same
        response — one connection per request, closed the moment the job
        completes or fails. Shared by every "run a job, watch it inline"
        endpoint (session import, project upload)."""
        connection = self._broadcaster.connect(job.username)
        self.submit(job)

        async def stream():
            try:
                while True:
                    message = await connection.get()
                    if message["queue_status"] == "exited":
                        if message["job_status"] == "completed" and job.result:
                            message = {**message, "result": json.loads(job.result)}
                        yield f"data: {json.dumps(message)}\n\n"
                        return
                    yield f"data: {json.dumps(message)}\n\n"
            finally:
                self._broadcaster.disconnect(job.username, connection)

        return StreamingResponse(stream(), media_type="text/event-stream")
