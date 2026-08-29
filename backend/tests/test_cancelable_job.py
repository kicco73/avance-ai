from __future__ import annotations

import pytest

from jobs import CancelableJob

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


def _link(job: CancelableJob, parent: CancelableJob | None = None) -> None:
    """Mirrors JobQueue.submit()'s own prepare/registration logic, without
    a real queue or worker threads, so these tests can build a dependency
    graph and assert on it deterministically."""
    if not job.is_pending():
        if parent is not None:
            job._add_parent_job(parent)
        return
    children = job.prepare(parent)
    for child in children:
        _link(child, parent=job)


def test_abort_on_a_standalone_job_marks_it_aborted():
    a = _Node("a")
    _link(a)

    a.cancel()

    assert a.is_aborted()


def test_status_reports_aborted_after_abort():
    a = _Node("a")
    _link(a)

    a.cancel()

    assert a.status() is CancelableJob.STATUS.aborted


def test_aborting_a_root_cascades_to_its_only_dependency():
    b = _Node("b")
    a = _Node("a", [b])
    _link(a)

    a.cancel()

    assert a.is_aborted()
    assert b.is_aborted()


def test_an_independently_launched_dependency_survives_its_only_dependent_being_aborted():
    """B, A->B: A merely depends on B, but B was also launched on its own
    -- cancelling A must not take B down with it."""
    b = _Node("b")
    _link(b)
    a = _Node("a", [b])
    _link(a)

    a.cancel()

    assert a.is_aborted()
    assert not b.is_aborted()


def test_a_shared_dependency_survives_while_another_root_still_needs_it():
    """A->B, C->B: cancelling A alone must leave B running for C's sake."""
    b = _Node("b")
    a = _Node("a", [b])
    c = _Node("c", [b])
    _link(a)
    _link(c)

    a.cancel()

    assert a.is_aborted()
    assert not b.is_aborted()
    assert not c.is_aborted()


def test_a_shared_dependency_aborts_once_every_root_that_needs_it_is_gone():
    b = _Node("b")
    a = _Node("a", [b])
    c = _Node("c", [b])
    _link(a)
    _link(c)

    a.cancel()
    c.cancel()

    assert a.is_aborted()
    assert c.is_aborted()
    assert b.is_aborted()


def test_force_aborting_a_shared_dependency_cascades_upward_to_every_root_that_needs_it():
    """abort() is an explicit, forced command: cancelling B directly must
    take A and C down too, since neither can proceed without it -- not
    just orphan B while leaving A/C running forever waiting on it."""
    b = _Node("b")
    a = _Node("a", [b])
    c = _Node("c", [b])
    _link(a)
    _link(c)

    b.cancel()

    assert b.is_aborted()
    assert a.is_aborted()
    assert c.is_aborted()


def test_a_diamond_shaped_graph_cascades_fully_when_the_root_aborts():
    b = _Node("b")
    x = _Node("x", [b])
    y = _Node("y", [b])
    a = _Node("a", [x, y])
    _link(a)

    a.cancel()

    assert a.is_aborted()
    assert x.is_aborted()
    assert y.is_aborted()
    assert b.is_aborted()


async def test_run_next_step_refuses_to_run_an_aborted_job():
    a = _Node("a")
    _link(a)

    a.cancel()

    with pytest.raises(ValueError):
        await a.run_next_step()
    assert not a.ran
