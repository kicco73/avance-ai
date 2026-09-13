from __future__ import annotations

import pytest

from jobs import CancelableJob
from jobs.job_queue import JobQueue
from system.broadcaster import Broadcaster

pytestmark = pytest.mark.contract


class _Node(CancelableJob):
    def __init__(self, key: str, dependencies: list["_Node"] | None = None) -> None:
        super().__init__(key=key, username="test")
        self._dependencies = dependencies or []
        self.ran = False

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, tuple(self._dependencies)

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        self.ran = True


def _queue_with_no_workers() -> JobQueue:
    return JobQueue(max_concurrent=0, broadcaster=Broadcaster())


async def test_cancelling_a_standalone_job_aborts_it_and_it_refuses_to_run_afterwards():
    a = _Node("a")
    _queue_with_no_workers().submit(a)

    a.cancel()

    assert a.is_aborted()
    assert a.status() is CancelableJob.STATUS.aborted
    with pytest.raises(ValueError):
        await a.run_next_step()
    assert not a.ran


def test_cancelling_a_root_cascades_down_its_whole_dependency_graph():
    b = _Node("b")
    x = _Node("x", [b])
    y = _Node("y", [b])
    a = _Node("a", [x, y])
    _queue_with_no_workers().submit(a)

    a.cancel()

    assert a.is_aborted()
    assert x.is_aborted()
    assert y.is_aborted()
    assert b.is_aborted()


def test_a_dependency_survives_while_anything_else_still_needs_it_and_aborts_once_nothing_does():
    """A->B, C->B: cancelling A alone must leave B running for C's sake;
    a job launched on its own is never taken down by a dependent either."""
    job_queue = _queue_with_no_workers()
    independent = _Node("b")
    job_queue.submit(independent)
    depends_on_it = _Node("a", [independent])
    job_queue.submit(depends_on_it)

    depends_on_it.cancel()
    assert depends_on_it.is_aborted()
    assert not independent.is_aborted()

    shared = _Node("b")
    a = _Node("a", [shared])
    c = _Node("c", [shared])
    job_queue.submit(a)
    job_queue.submit(c)

    a.cancel()
    assert a.is_aborted()
    assert not shared.is_aborted()
    assert not c.is_aborted()

    c.cancel()
    assert c.is_aborted()
    assert shared.is_aborted()


def test_force_aborting_a_shared_dependency_cascades_upward_to_every_root_that_needs_it():
    """abort() is an explicit, forced command: cancelling B directly must
    take A and C down too, since neither can proceed without it -- not
    just orphan B while leaving A/C running forever waiting on it."""
    job_queue = _queue_with_no_workers()
    b = _Node("b")
    a = _Node("a", [b])
    c = _Node("c", [b])
    job_queue.submit(a)
    job_queue.submit(c)

    b.cancel()

    assert b.is_aborted()
    assert a.is_aborted()
    assert c.is_aborted()
