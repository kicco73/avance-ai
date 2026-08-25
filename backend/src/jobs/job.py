from __future__ import annotations

from abc import ABC, abstractmethod

# Instructions for Claude Code: DO NOT TOUCH THIS FILE

class Job(ABC):

    def __init__(self, key: str, username: str) -> None:
        self.key = key
        self.username = username
        self._steps_done: int = 0
        self._total_steps: int | None = None
        self._is_failed = False
        self._error: str | None = None

    @abstractmethod
    def _prepare(self) -> tuple[int, list["Job"]]:
        raise NotImplementedError

    def prepare(self) -> list["Job"]:
        if self._total_steps is not None:
            raise ValueError(f"Job {self} is already prepared")
        self._total_steps, dependencies = self._prepare()
        return dependencies

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

    def status(self) -> str:
        if self._total_steps is None:
            return 'pending'
        if self.is_failed():
            return 'failed'
        if self.is_done():
            return 'completed'
        return 'running'

    def is_done(self) -> bool:
        return self.progress() >= 100

    def is_failed(self) -> bool:
        return self._is_failed

    def error(self) -> str | None:
        return self._error

    def progress(self) -> float:
        return self._steps_done * 100.0 / self._total_steps if self._total_steps else 0.0

    async def run_next_step(self) -> None:
        if not self._total_steps:
            raise ValueError(f"Job {self} not prepared")
        try:
            self._is_failed = False
            await self._run_next_step()
        except Exception as exc:
            self._is_failed = True
            self._error = str(exc)
            raise
        self._steps_done += 1
