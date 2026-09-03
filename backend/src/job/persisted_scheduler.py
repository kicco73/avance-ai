"""A Scheduler whose queue is the Task table — nothing lives in memory.

    submit(task, when) ──> INSERT Task row (pending, run_at=when) ──> notify
    thread loop        ──> claim_due_task(now)  [atomic pending -> dispatched]
                            └──> hydrator[type](key, username, payload) ──> JobQueue.submit
                       ──> else sleep until next_task_due_at (capped by poll_interval)
    task settles       ──> settle_task(key, done|failed)
    cancel(task)       ──> cancel_task(key) if still pending, else JobQueue.cancel

Every decision reads the table at the moment it is taken, so whatever
happened to a row in between — a project deleted (its rows cascade
away), a user erased, a manual UPDATE, another process claiming it —
is simply what the scheduler sees next. A task is hydrated only when
it is about to run, never at boot, so no live object waits for days.

The scheduler is ignorant of what a task *does*: it only knows a
TYPE -> hydrator mapping. What "the environment the task carries"
means, and how to rebuild it faithfully, is the hydrator's problem
(see tracking/actuators/deferred_task.py for the actuator.defer one).

Delivery semantics: the claim is an atomic UPDATE guarded on
status='pending', so two schedulers over the same database (two
threads, two backend instances) never claim the same row. A row still
`dispatched` after `lease_seconds` with no settlement belongs to a
process that died mid-run: it goes back to pending and runs again
(at-least-once), logged as such — checked at start and on every poll,
never by blindly requeueing whatever is dispatched at boot, which
would re-run a task another live instance is executing right now. A
row whose type has no hydrator, or whose hydrator refuses it, is
marked failed with the reason, never silently dropped."""
from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, TYPE_CHECKING

from jobs.job import CancelableJob, DependentJob
from jobs.job_queue import AbstractJobQueue
from jobs.scheduler import Scheduler
from jobs.task import Task
from logging_factory import LoggerFactory

if TYPE_CHECKING:
    from db import Db

logger = LoggerFactory.get_logger(__name__)

Hydrator = Callable[[str, str, dict[str, Any]], Task]


class PersistedScheduler(Scheduler):

    def __init__(
        self, queue: AbstractJobQueue, db: "Db", hydrators: dict[str, Hydrator], *,
        poll_interval_seconds: float = 60.0, lease_seconds: float = 600.0,
    ) -> None:
        self._queue = queue
        self._db = db
        # Shared with the owner, which may keep registering types until start().
        self._hydrators = hydrators
        self._poll_interval = poll_interval_seconds
        # How long a claimed row may stay unsettled before it is presumed
        # orphaned. Longer than any task honestly takes to run.
        self._lease = timedelta(seconds=lease_seconds)
        self._wakeup = threading.Condition(threading.Lock())
        self._thread: threading.Thread | None = None
        self._stopping = False

    def start(self) -> None:
        """Begins claiming due rows. Before this, submit() only ever
        adds rows — a process still wiring itself up claims nothing."""
        if self._thread is not None:
            return
        self._recover_stale_claims()
        self._thread = threading.Thread(target=self._run, name="persisted-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stops claiming (the loop exits at its next wake-up). Rows are
        untouched — another scheduler, or this process restarted, picks
        them up. Tests need this: the process-global database proxy is
        rebound per test, and a still-polling thread would claim the
        next test's rows."""
        with self._wakeup:
            self._stopping = True
            self._wakeup.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    # --- Scheduler -------------------------------------------------------

    def submit(self, job: DependentJob, *, timestamp: datetime | None = None) -> None:
        if not isinstance(job, Task):
            raise TypeError(
                f"PersistedScheduler only schedules jobs.Task instances (got {type(job).__name__}) — "
                "anything scheduled here must survive a restart."
            )
        if job.TYPE not in self._hydrators:
            raise ValueError(f"Task {job.key} is of type '{job.TYPE}' but no hydrator is registered for it.")
        when = self._as_utc(timestamp) or datetime.now(timezone.utc)
        self._db.create_task(
            job.key, job.TYPE, job.username, job.project_id, when, job.dehydrate(), job.ui_label, job.ui_description,
        )
        with self._wakeup:
            self._wakeup.notify()

    def cancel(self, job: CancelableJob) -> None:
        if self._db.cancel_task(job.key):
            return
        # No longer pending: already handed to the queue (or settled, in
        # which case this is a harmless no-op there too).
        self._queue.cancel(job)

    def poke(self) -> None:
        """Re-read the table now rather than at the next poll — for a
        caller that changed rows behind the scheduler's back."""
        with self._wakeup:
            self._wakeup.notify()

    # --- the loop --------------------------------------------------------

    def _recover_stale_claims(self) -> None:
        requeued = self._db.requeue_stale_dispatched_tasks(datetime.now(timezone.utc) - self._lease)
        if requeued:
            logger.warning(
                "%d task(s) were claimed over %s ago and never settled — presumed orphaned by a dead process, "
                "running them again: %s", len(requeued), self._lease, ", ".join(requeued),
            )

    def _run(self) -> None:
        while not self._stopping:
            try:
                self._recover_stale_claims()
                row = self._db.claim_due_task(datetime.now(timezone.utc))
            except Exception as exc:  # the database being briefly unavailable must not kill the loop
                logger.exception("PersistedScheduler could not read the Task table: %s", exc)
                row = None
                due = None
            else:
                if row is not None:
                    self._dispatch(row)
                    continue
                due = self._db.next_task_due_at()
            wait = self._poll_interval
            if due is not None:
                wait = max(0.0, min(wait, (due - datetime.now(timezone.utc)).total_seconds()))
            with self._wakeup:
                if not self._stopping:
                    self._wakeup.wait(wait)

    def _dispatch(self, row: dict[str, Any]) -> None:
        key = row["key"]
        hydrator = self._hydrators.get(row["type"])
        if hydrator is None:
            self._db.settle_task(key, "failed", f"no hydrator registered for task type '{row['type']}'")
            logger.error("Task %s: no hydrator registered for type '%s' — marked failed.", key, row["type"])
            return
        try:
            task = hydrator(key, row["username"], row["payload"])
        except Exception as exc:
            self._db.settle_task(key, "failed", f"hydration failed: {exc}")
            logger.exception("Task %s could not be hydrated — marked failed: %s", key, exc)
            return
        task.set_settlement_listener(self._on_settled)
        try:
            self._queue.submit(task)
        except Exception as exc:
            self._db.settle_task(key, "failed", f"could not be queued: {exc}")
            logger.exception("Task %s could not be queued — marked failed: %s", key, exc)

    def _on_settled(self, task: Task, status: str, error: str | None) -> None:
        try:
            self._db.settle_task(task.key, status, error)
        except Exception as exc:
            logger.exception("Task %s settled (%s) but its row could not be updated: %s", task.key, status, exc)
