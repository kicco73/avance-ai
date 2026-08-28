from __future__ import annotations

import threading
import time
from collections import deque
from typing import ClassVar, TYPE_CHECKING

from logging_factory import LoggerFactory

from .job import Job
from .job_queue import JobQueue

if TYPE_CHECKING:
    from testing.queue_progress_broadcaster import QueueProgressBroadcaster


logger = LoggerFactory.get_logger(__name__)

class ThrottledJobQueue(JobQueue):
    """
    Note: this class may introduce a randomness in the order of jobs
    that are being throttled since lock() does not guarantee fairness.
    """

    class STATUS(JobQueue.STATUS):
        paused: ClassVar["ThrottledJobQueue.STATUS"]

    STATUS.paused = STATUS("paused")

    def __init__(
        self,
        max_concurrent: int,
        broadcaster: "QueueProgressBroadcaster",
        max_jobs_per_minute: int,
        min_job_interval_ms: int,
    ) -> None:
        self.__throttle_lock = threading.Lock()
        self.__run_times: deque[float] = deque()
        self.__last_run_at: float | None = None
        self.__max_jobs_per_minute = max_jobs_per_minute
        self.__min_job_interval_ms = min_job_interval_ms
        super().__init__(max_concurrent, broadcaster)

    def __wait_until_allowed(self, job: Job) -> None:
        while True:
            with self.__throttle_lock:
                now = time.monotonic()
                while self.__run_times and now - self.__run_times[0] >= 60:
                    self.__run_times.popleft()

                wait_seconds = 0.0
                if len(self.__run_times) >= self.__max_jobs_per_minute:
                    wait_seconds = max(wait_seconds, self.__run_times[0] + 60 - now)

                if self.__last_run_at is not None:
                    elapsed_ms = (now - self.__last_run_at) * 1000
                    if elapsed_ms < self.__min_job_interval_ms:
                        wait_seconds = max(wait_seconds, (self.__min_job_interval_ms - elapsed_ms) / 1000)

                if wait_seconds <= 0:
                    self.__run_times.append(now)
                    self.__last_run_at = now
                    return

            logger.info(f"waiting {wait_seconds}s")
            self._broadcast_status(job, self.STATUS.paused)
            time.sleep(wait_seconds)

    def _dequeue_and_notify(self) -> Job:
        job = self._dequeue()

        if job.is_background:
            self.__wait_until_allowed(job)

        self._broadcast_status(job, self.STATUS.running)
        return job
