from __future__ import annotations

import pytest

from jobs import DependentJob
from jobs.job_queue import JobQueue
from system.broadcaster import Broadcaster

pytestmark = pytest.mark.contract


class _Node(DependentJob):
    def __init__(self, key: str, dependencies: list["_Node"] | None = None) -> None:
        super().__init__(key=key, username="test")
        self._dependencies = dependencies or []
        self.ran = False

    def _prepare(self) -> tuple[int, tuple[DependentJob, ...]]:
        return 1, tuple(self._dependencies)

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        self.ran = True


class _RaisingNode(_Node):
    def __init__(self, key: str, message: str) -> None:
        super().__init__(key)
        self._message = message

    async def _run_next_step(self) -> None:
        raise ValueError(self._message)


def _queue_with_no_workers() -> JobQueue:
    return JobQueue(max_concurrent=0, broadcaster=Broadcaster())


async def test_preparing_records_the_topology_both_ways_and_a_job_with_no_dependencies_runs_at_once():
    b = _Node("b")
    a = _Node("a", [b])
    _queue_with_no_workers().submit(a)

    assert a.children == (b,)
    assert b.parents == (a,)

    job_queue = JobQueue(max_concurrent=1, broadcaster=Broadcaster())
    standalone = _Node("standalone")
    job_queue.submit(standalone)
    await job_queue.wait_for(standalone)

    assert standalone.is_done()
    assert standalone.ran


async def test_failing_cascades_to_every_parent_that_needs_it_and_keeps_the_first_error():
    b = _RaisingNode("b", "boom")
    d = _RaisingNode("d", "boom again")
    a = _Node("a", [b, d])
    c = _Node("c", [b])
    job_queue = _queue_with_no_workers()
    job_queue.submit(a)
    job_queue.submit(c)

    with pytest.raises(ValueError):
        await b.run_next_step()

    assert b.is_failed()
    assert b.error() == "boom"
    assert a.is_failed()
    assert a.error() == "dependency b failed"
    assert c.is_failed()
    assert c.error() == "dependency b failed"

    with pytest.raises(ValueError):
        await d.run_next_step()

    assert d.error() == "boom again"
    assert a.error() == "dependency b failed"
