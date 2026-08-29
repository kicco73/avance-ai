from __future__ import annotations

from abc import ABC, abstractmethod
import threading
from logging_factory import LoggerFactory
from try_again_error import TryAgainError
from enum import Enum

logger = LoggerFactory.get_logger(__name__)

# Instructions for Claude Code: DO NOT TOUCH THIS FILE

class Job(ABC):

    class STATUS(Enum):
        pending = "pending"
        running = "running"
        requeued = "requeued"
        completed = "completed"
        failed = "failed"
        aborted = "aborted"

    def __init__(self, key: str, username: str) -> None:
        self.key = key
        self.username = username
        self.__steps_done: int = 0
        self.__total_steps: int | None = None
        self.__is_failed = False
        self.__is_aborted = False
        self.__try_again = False
        self.__children_settled = False
        self.__error: str | None = None
        self.__children: list["Job"] = []
        self.__parents: list["Job"] = []
        self.__lock = threading.RLock()

    @abstractmethod
    def _prepare(self) -> tuple[int, tuple["Job"]]:
        raise NotImplementedError

    def prepare(self, parent_job: Job | None = None) -> tuple["Job", ...]:
        if self.__total_steps is not None:
            raise ValueError(f"Job {self} is already prepared")
        self.__total_steps, children = self._prepare()
        self.__children = list(children)
        assert (self.__total_steps)
        with self.__lock:
            self.__parents.append(parent_job or self)
        return children

    @property
    def children(self) -> tuple["Job", ...]:
        return tuple(self.__children)

    @property
    def parents(self) -> tuple["Job", ...]:
        return tuple(self.__parents)

    def _dependency_resolved(self, dep: "Job") -> bool:
        with self.__lock:
            if dep not in self.__children:
                return False
            self.__children.remove(dep)
            return self.__children_settled and not self.__children

    def _children_registered(self) -> bool:
        with self.__lock:
            self.__children_settled = True
            return not self.__children

    def _add_parent_job(self, job: Job) -> bool:
        with self.__lock:
            self.__parents.append(job)
            return self.is_done() or self.is_failed()

    def _remove_parent_job(self, job: Job) -> None:
        with self.__lock:
            self.__parents.remove(job)
            if not self.__parents:
                # I have no father depending on myself, I'm orphan.
                # Aborting.
                self.__is_aborted = True
                # removing myself from my children, they are no more needed.
                for dep in self.__children:
                    dep._remove_parent_job(self)

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
        if self.is_aborted():
            return self.STATUS.aborted
        if self.is_failed():
            return self.STATUS.failed
        if self.is_done():
            return self.STATUS.completed
        if self.__try_again:
            return self.STATUS.requeued
        return self.STATUS.running

    def is_done(self) -> bool:
        return self.progress() >= 100

    def is_aborted(self) -> bool:
        return self.__is_aborted

    def is_failed(self) -> bool:
        return self.__is_failed

    def is_pending(self) -> bool:
        return self.__total_steps is None

    def error(self) -> str | None:
        return self.__error

    def progress(self) -> float:
        return self.__steps_done * 100.0 / self.__total_steps if self.__total_steps else 0.0

    def abort(self) -> None:
        with self.__lock:
            parents = list(self.__parents)
        for p in parents:
            if p is not self:
                p.abort()
        with self.__lock:
            while self.__parents:
                self._remove_parent_job(self.__parents[0])

    def _fail(self, error: str) -> None:
        with self.__lock:
            if self.__is_failed:
                return
            self.__is_failed = True
            self.__error = error
            parents = list(self.__parents)
        for p in parents:
            if p is not self:
                p._fail(f"dependency {self.key} failed")

    async def run_next_step(self) -> None:
        if not self.__total_steps:
            raise ValueError(f"Job {self} not prepared")
        if self.__is_aborted:
            raise ValueError(f"Job {self} is aborted")
        if self.__is_failed:
            raise ValueError(f"Job {self} is failed")
        self.__try_again = False
        try:
            await self._run_next_step()
        except TryAgainError:
            self.__try_again = True
            raise
        except Exception as exc:
            self._fail(str(exc))
            raise
        self.__steps_done += 1
