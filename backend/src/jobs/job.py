from __future__ import annotations

from abc import ABC, abstractmethod
import threading
from dataclasses import dataclass
from typing import cast, ClassVar
from logging_factory import LoggerFactory
from try_again_error import TryAgainError

logger = LoggerFactory.get_logger(__name__)

# Instructions for Claude Code: DO NOT TOUCH THIS FILE

class Job(ABC):

    @dataclass(frozen=True)
    class STATUS:
        value: str
        pending: ClassVar["Job.STATUS"]
        running: ClassVar["Job.STATUS"]
        requeued: ClassVar["Job.STATUS"]
        completed: ClassVar["Job.STATUS"]
        failed: ClassVar["Job.STATUS"]

    STATUS.pending = STATUS("pending")
    STATUS.running = STATUS("running")
    STATUS.requeued = STATUS("requeued")
    STATUS.completed = STATUS("completed")
    STATUS.failed = STATUS("failed")

    def __init__(self, key: str, username: str) -> None:
        self.key = key
        self.username = username
        self.__steps_done: int = 0
        self.__total_steps: int | None = None
        self.__is_failed = False
        self.__try_again = False
        self.__error: str | None = None

    @abstractmethod
    def _prepare(self) -> tuple[int, tuple["Job"]]:
        raise NotImplementedError

    def prepare(self) -> tuple["Job", ...]:
        if self.__total_steps is not None:
            raise ValueError(f"Job {self} is already prepared")
        self.__total_steps, children = self._prepare()
        assert (self.__total_steps)
        return children

    @property
    def is_background(self) -> bool:
        return True

    @property
    @abstractmethod
    def result(self) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def _run_next_step(self) -> None:
        raise NotImplementedError

    def status(self) -> Job.STATUS:
        if self.is_pending():
            return self.STATUS.pending
        if self.is_failed():
            return self.STATUS.failed
        if self.is_done():
            return self.STATUS.completed
        if self.is_requeued():
            return self.STATUS.requeued
        return self.STATUS.running

    def is_done(self) -> bool:
        return self.progress() >= 100

    def is_requeued(self) -> bool:
        return self.__try_again

    def is_failed(self) -> bool:
        return self.__is_failed

    def is_pending(self) -> bool:
        return self.__total_steps is None

    def error(self) -> str | None:
        return self.__error

    def progress(self) -> float:
        return self.__steps_done * 100.0 / self.__total_steps if self.__total_steps else 0.0

    def _fail(self, error: str) -> bool:
        if self.__is_failed:
            return False
        self.__is_failed = True
        self.__error = error
        return True

    async def run_next_step(self) -> None:
        if not self.__total_steps:
            raise ValueError(f"Job {self} not prepared")
        if self.__is_failed:
            raise ValueError(f"Job {self} is failed")
        self.__try_again = False
        try:
            await self._run_next_step()
        except TryAgainError:
            self.__try_again = True
            return
        except Exception as exc:
            self._fail(str(exc))
            raise
        self.__steps_done += 1


class DependentJob(Job):
    """A Job that participates in a dependency graph: tracks the jobs it
    depends on (children) and the jobs that depend on it (parents), so
    JobQueue can tell when a waiter's dependencies have all resolved, and
    so a failure propagates to every job that needs this one."""

    def __init__(self, key: str, username: str) -> None:
        super().__init__(key, username)
        self.__children_settled = False
        self.__children: list[DependentJob] = []
        self.__parents: list[DependentJob] = []
        self.__lock = threading.RLock()

    def prepare(self, parent_job: "DependentJob | None" = None) -> tuple["DependentJob", ...]:
        children = cast(tuple["DependentJob", ...], super().prepare())
        self.__children = list(children)
        with self.__lock:
            self.__parents.append(parent_job or self)
        return children

    @property
    def children(self) -> tuple["DependentJob", ...]:
        return tuple(self.__children)

    @property
    def parents(self) -> tuple["DependentJob", ...]:
        return tuple(self.__parents)

    def _dependency_resolved(self, dep: "DependentJob") -> bool:
        with self.__lock:
            if dep not in self.__children:
                return False
            self.__children.remove(dep)
            return self.__children_settled and not self.__children

    def _children_registered(self) -> bool:
        with self.__lock:
            self.__children_settled = True
            return not self.__children

    def _add_parent_job(self, job: "DependentJob") -> bool:
        with self.__lock:
            self.__parents.append(job)
            return self.is_done() or self.is_failed()

    def _remove_parent(self, job: "DependentJob") -> bool:
        """Removes job from this job's parents; returns True if that was
        the last one -- nothing still needs this job."""
        with self.__lock:
            self.__parents.remove(job)
            return not self.__parents

    def _fail(self, error: str) -> bool:
        with self.__lock:
            newly_failed = super()._fail(error)
            parents = list(self.__parents)
        if not newly_failed:
            return False
        for p in parents:
            if p is not self:
                p._fail(f"dependency {self.key} failed")
        return True


class CancelableJob(DependentJob):
    """A DependentJob that can also be explicitly cancelled: abort()
    cascades up to every job that still needs this one (they can't
    proceed without it either), and down to a dependency once nothing
    needs it anymore."""

    class STATUS(Job.STATUS):
        aborted: ClassVar["CancelableJob.STATUS"]

    STATUS.aborted = STATUS("aborted")

    def __init__(self, key: str, username: str) -> None:
        super().__init__(key, username)
        self.__is_aborted = False

    @property
    def children(self) -> tuple[CancelableJob, ...]:
        return cast(tuple[CancelableJob, ...], super().children)

    @property
    def parents(self) -> tuple[CancelableJob, ...]:
        return cast(tuple[CancelableJob, ...], super().parents)

    def is_aborted(self) -> bool:
        return self.__is_aborted

    def status(self) -> Job.STATUS:
        if self.is_aborted():
            return self.STATUS.aborted
        return super().status()

    def abort(self) -> None:
        for p in self.parents:
            if p is not self:
                p.abort()
        while self.parents:
            self._remove_parent_job(self.parents[0])

    def _remove_parent_job(self, job: CancelableJob) -> None:
        if self._remove_parent(job):
            # I have no father depending on myself, I'm orphan.
            # Aborting.
            self.__is_aborted = True
            # removing myself from my children, they are no more needed.
            for dep in self.children:
                dep._remove_parent_job(self)

    async def run_next_step(self) -> None:
        if self.is_aborted():
            raise ValueError(f"Job {self} is aborted")
        await super().run_next_step()
