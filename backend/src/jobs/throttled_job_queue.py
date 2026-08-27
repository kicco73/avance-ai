from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from .job import Job
from .job_queue import JobQueue

if TYPE_CHECKING:
    from testing.queue_progress_broadcaster import QueueProgressBroadcaster


class ThrottledJobQueue(JobQueue):
    """
    Note: this class may introduce a randomness in the order of jobs
    that are being throttled since lock() does not guarantee fairness.
    """
    def __init__(
        self,
        max_concurrent: int,
        broadcaster: "QueueProgressBroadcaster",
        max_jobs_per_minute: int,
        min_job_interval_ms: int,
    ) -> None:
        self.__throttle_lock = threading.Lock()
        self.__window_start = time.monotonic()
        self.__window_count = 0
        self.__last_run_at: float | None = None
        self.__max_jobs_per_minute = max_jobs_per_minute
        self.__min_job_interval_ms = min_job_interval_ms
        super().__init__(max_concurrent, broadcaster)

    def _throttle(self) -> None:
        while True:
            with self.__throttle_lock:
                now = time.monotonic()
                if now - self.__window_start >= 60:
                    self.__window_start = now
                    self.__window_count = 0

                wait_seconds = 0.0
                if self.__window_count >= self.__max_jobs_per_minute:
                    wait_seconds = max(wait_seconds, self.__window_start + 60 - now)

                if self.__last_run_at is not None:
                    elapsed_ms = (now - self.__last_run_at) * 1000
                    if elapsed_ms < self.__min_job_interval_ms:
                        wait_seconds = max(wait_seconds, (self.__min_job_interval_ms - elapsed_ms) / 1000)

                if wait_seconds <= 0:
                    self.__window_count += 1
                    self.__last_run_at = now
                    return

            time.sleep(wait_seconds)

    def _dequeue(self) -> Job:
        self._throttle()
        return super()._dequeue()
