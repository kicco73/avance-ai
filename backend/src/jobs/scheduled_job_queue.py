from __future__ import annotations

import bisect
import itertools
import threading
from datetime import datetime, timezone

from logging_factory import LoggerFactory

from .job import CancelableJob, DependentJob
from .job_queue import AbstractJobQueue

logger = LoggerFactory.get_logger(__name__)


class ScheduledJobQueue(AbstractJobQueue):

    def __init__(self, queue: AbstractJobQueue) -> None:
        self.__queue = queue
        self.__lock = threading.Lock()
        self.__wakeup = threading.Condition(self.__lock)
        self.__pending: list[tuple[datetime, int, DependentJob, DependentJob | None]] = []
        self.__counter = itertools.count()
        threading.Thread(target=self.__run, name="scheduled-job-queue", daemon=True).start()

    def submit(self, job: DependentJob, parent: DependentJob | None = None, *, timestamp: datetime | None = None) -> None:
        if timestamp is None or timestamp <= datetime.now(timezone.utc):
            self.__queue.submit(job, parent)
            return

        with self.__wakeup:
            bisect.insort(self.__pending, (timestamp, next(self.__counter), job, parent))
            self.__wakeup.notify()

    def cancel(self, job: CancelableJob) -> None:
        with self.__wakeup:
            for i, entry in enumerate(self.__pending):
                if entry[2] is job:
                    self.__pending.pop(i)
                    return
        self.__queue.cancel(job)

    async def wait_for(self, job: DependentJob) -> None:
        await self.__queue.wait_for(job)

    def __run(self) -> None:
        while True:
            with self.__wakeup:
                while not self.__pending:
                    self.__wakeup.wait()

                timestamp, _, job, parent = self.__pending[0]
                wait_seconds = (timestamp - datetime.now(timezone.utc)).total_seconds()
                if wait_seconds > 0:
                    self.__wakeup.wait(wait_seconds)
                    continue

                self.__pending.pop(0)

            self.__queue.submit(job, parent)
