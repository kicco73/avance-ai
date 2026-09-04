from __future__ import annotations

import uuid
from abc import abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

from .job import CancelableJob

# What a PersistedScheduler hears back once a task has settled (see
# Task.run_next_step): the task, its terminal status ('done'|'failed')
# and, on failure, the error text.
SettlementListener = Callable[["Task", str, "str | None"], None]


class Task(CancelableJob):
    """A long-running, scheduled unit of work that outlives the process:
    a CancelableJob that can be hibernated to a row of the Task table
    (see db/models.py) and rebuilt from it. The contract is that
    (TYPE, username, project_id, payload) is *all* a Task is — no live
    object reference survives a restart, so `dehydrate()` returns pure
    JSON data and the hydrator registered for TYPE on the scheduler
    turns it back into an equivalent instance.

    Every Task belongs to a user and a project (both real foreign keys
    on the row, cascading on delete: erase the user or delete the
    project and its pending tasks are gone with it, no code involved)
    and describes itself for the UI through ui_label/ui_description,
    stored on the row at submit time so a listing never needs to
    hydrate anything.

    Settlement is reported from inside run_next_step rather than by the
    queue (jobs/job_queue.py is closed to changes), so the scheduler
    learns of done/failed without the queue knowing about persistence."""

    #: Registry key selecting the hydrator for rows of this kind.
    TYPE: ClassVar[str]

    @classmethod
    def make_key(cls, id: str | int | None = None) -> str:
        """A stable "ClassName.<id>" key when a caller names one — the
        same id addresses the same row again later (PersistedScheduler's
        db.get_task/reschedule_task/cancel_task), so a follow-up "touch"
        can find and reschedule it instead of leaving a random duplicate
        behind. With no id, today's untraceable-by-design key."""
        if id is not None:
            return f"{cls.__name__}.{id}"
        return f"{cls.TYPE}:{uuid.uuid4()}"

    def __init__(self, key: str, username: str) -> None:
        super().__init__(key, username)
        self.__settlement_listener: SettlementListener | None = None

    @property
    @abstractmethod
    def project_id(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def ui_label(self) -> str:
        """One line naming this task for a person — what, for whom."""
        raise NotImplementedError

    @property
    @abstractmethod
    def ui_description(self) -> str:
        """A sentence or two on what will happen when it runs."""
        raise NotImplementedError

    @abstractmethod
    def dehydrate(self) -> dict[str, Any]:
        """JSON-serialisable description sufficient to rebuild this task."""
        raise NotImplementedError

    def set_settlement_listener(self, listener: SettlementListener | None) -> None:
        self.__settlement_listener = listener

    def _settle(self, status: str, error: str | None = None) -> None:
        listener, self.__settlement_listener = self.__settlement_listener, None
        if listener is not None:
            listener(self, status, error)

    async def run_next_step(self) -> None:
        try:
            await super().run_next_step()
        except Exception as exc:
            self._settle("failed", str(exc))
            raise
        if self.is_failed():
            # Retries (TryAgainError) exhausted: Job._fail'd without raising.
            self._settle("failed", self.error())
        elif self.is_done():
            self._settle("done")
