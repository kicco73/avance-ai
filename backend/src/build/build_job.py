"""A build, as the job queue understands it.

A backend copy takes minutes — it copies a tree, compiles an automaton,
prunes a database and then runs the built backend's whole test suite —
so it cannot be a request that returns when it is done. It is a Job with
one step per phase, submitted through the SchedulerService, and the
response streams its progress (see SchedulerService.stream_progress).

What the Build view draws comes from `result`, which every step
broadcasts: the step table with each step's state, worked out from how
many have run rather than recorded as anyone changes it.
"""
from __future__ import annotations

import json
import uuid

from jobs import CancelableJob
from system.logging_factory import LoggerFactory

from .backend_copy import STEPS, BackendCopy

logger = LoggerFactory.get_logger(__name__)

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


class BuildJob(CancelableJob):

    def __init__(self, copy: BackendCopy) -> None:
        super().__init__(key="build", username=f"build:{uuid.uuid4().hex}")
        self._copy = copy
        self._done_steps = 0

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return len(STEPS), ()

    @property
    def is_background(self) -> bool:
        """Somebody is watching this one stream: it goes to the head of
        the queue, like every other job a person is waiting on."""
        return False

    @property
    def result(self) -> str | None:
        return json.dumps({**self._copy.report(), "steps": self._steps()})

    def _steps(self) -> list[dict]:
        return [
            {"key": step.key, "label": step.label, "status": self._status_of(index)}
            for index, step in enumerate(STEPS)
        ]

    def _status_of(self, index: int) -> str:
        """Derived from how far the build got, never written down: the
        steps before the current one ran, the current one is running or
        is where it stopped, and the rest have not started."""
        if index < self._done_steps:
            return DONE
        if index > self._done_steps:
            return PENDING
        return FAILED if self.is_failed() else RUNNING

    async def _run_next_step(self) -> None:
        STEPS[self._done_steps].run(self._copy)
        self._done_steps += 1

    def _fail(self, error: str) -> bool:
        """A build that stopped half-assembled leaves nothing behind, and
        the last good build of this project stays where it was: `publish`
        is what replaces it, and a failure before that never got there.
        A failure after it has nothing left to discard — which is what a
        suite that fails on a finished build should leave: the build, and
        the report saying its own tests do not pass."""
        newly_failed = super()._fail(error)
        if newly_failed:
            logger.warning("Build of '%s' failed at step %s: %s", self._copy.project_id, self._current_key(), error)
            self._copy.discard()
        return newly_failed

    def _current_key(self) -> str:
        return STEPS[min(self._done_steps, len(STEPS) - 1)].key
