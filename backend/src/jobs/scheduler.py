from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from .job import CancelableJob, DependentJob


class Scheduler(ABC):
    """Holds a job until its timestamp, then hands it to a JobQueue. Two
    implementations: InMemScheduler (a sorted list and a thread — gone
    with the process) and job.persisted_scheduler.PersistedScheduler
    (the Task table *is* the queue; nothing lives in memory). Both are
    private to the service that owns them, never handed to consumers."""

    @abstractmethod
    def submit(self, job: DependentJob, *, timestamp: datetime | None = None) -> None:
        """Runs `job` no earlier than `timestamp` (None, or a moment
        already past, means as soon as a worker is free). A naive
        timestamp is read as UTC, like every other timestamp in the system."""
        raise NotImplementedError

    @abstractmethod
    def cancel(self, job: CancelableJob) -> None:
        """Drops `job` whether it is still waiting for its time, queued,
        or running (a running job stops at its next step)."""
        raise NotImplementedError

    @staticmethod
    def _as_utc(timestamp: datetime | None) -> datetime | None:
        from datetime import timezone
        if timestamp is None or timestamp.tzinfo is not None:
            return timestamp
        return timestamp.replace(tzinfo=timezone.utc)
