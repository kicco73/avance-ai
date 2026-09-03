from __future__ import annotations

import bisect
import itertools
import threading
from datetime import datetime, timezone

from logging_factory import LoggerFactory

from .job import CancelableJob, DependentJob
from .job_queue import AbstractJobQueue
from .scheduler import Scheduler

logger = LoggerFactory.get_logger(__name__)


class InMemScheduler(Scheduler):
    """A sorted pending list and one thread that sleeps until the
    earliest entry is due. Nothing survives the process: what is still
    pending when it exits is simply lost — fine for a test, never for a
    platform job (see PersistedScheduler)."""

    def __init__(self, queue: AbstractJobQueue) -> None:
        self.__queue = queue
        self.__lock = threading.Lock()
        self.__wakeup = threading.Condition(self.__lock)
        self.__pending: list[tuple[datetime, int, DependentJob]] = []
        self.__counter = itertools.count()
        threading.Thread(target=self.__run, name="in-mem-scheduler", daemon=True).start()

    def submit(self, job: DependentJob, *, timestamp: datetime | None = None) -> None:
        timestamp = self._as_utc(timestamp)
        if timestamp is None or timestamp <= datetime.now(timezone.utc):
            self.__queue.submit(job)
            return

        with self.__wakeup:
            bisect.insort(self.__pending, (timestamp, next(self.__counter), job))
            self.__wakeup.notify()

    def cancel(self, job: CancelableJob) -> None:
        with self.__wakeup:
            for i, entry in enumerate(self.__pending):
                if entry[2] is job:
                    self.__pending.pop(i)
                    return
        self.__queue.cancel(job)

    def pending_jobs(self) -> tuple[DependentJob, ...]:
        """Snapshot of what is still waiting for its timestamp, in due order."""
        with self.__wakeup:
            return tuple(entry[2] for entry in self.__pending)

    def __run(self) -> None:
        while True:
            with self.__wakeup:
                while not self.__pending:
                    self.__wakeup.wait()

                timestamp, _, job = self.__pending[0]
                wait_seconds = (timestamp - datetime.now(timezone.utc)).total_seconds()
                if wait_seconds > 0:
                    self.__wakeup.wait(wait_seconds)
                    continue

                self.__pending.pop(0)

            try:
                self.__queue.submit(job)
            except Exception as exc:  # never let one bad job kill the scheduler thread
                logger.exception(f"InMemScheduler could not dispatch {job.key}: {exc}")
