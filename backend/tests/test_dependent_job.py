from __future__ import annotations

import pytest

from jobs import DependentJob

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


def _link(job: DependentJob, parent: DependentJob | None = None) -> None:
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


def _prepared_pair() -> tuple[_Node, _Node]:
    b = _Node("b")
    a = _Node("a", [b])
    a.prepare()
    return a, b


def test_preparing_records_the_topology_both_ways_and_a_job_with_no_dependencies_is_registered_at_once():
    b = _Node("b")
    a = _Node("a", [b])
    _link(a)

    assert a.children == (b,)
    assert b.parents == (a,)

    standalone = _Node("standalone")
    standalone.prepare()
    assert standalone._children_registered()


def test_readiness_needs_both_registration_and_resolution_whichever_arrives_last_and_only_once_per_dependency():
    """Mirrors a dependency finishing faster than submit()'s own recursive
    registration loop: the removal happens immediately, but readiness must
    wait for _children_registered() to confirm no more are coming."""
    resolved_first, b = _prepared_pair()
    assert not resolved_first._dependency_resolved(b)
    assert resolved_first._children_registered()

    registered_first, b2 = _prepared_pair()
    assert not registered_first._children_registered()
    assert registered_first._dependency_resolved(b2)
    # A one-shot signal — the same dependency never reports ready twice.
    assert not registered_first._dependency_resolved(b2)

    standalone = _Node("a")
    standalone.prepare()
    assert not standalone._dependency_resolved(_Node("stranger"))


async def test_add_parent_job_reports_whether_the_dependency_had_already_finished():
    done = _Node("b")
    done.prepare()
    await done.run_next_step()
    assert done.is_done()
    assert done._add_parent_job(_Node("late"))

    pending = _Node("b")
    pending.prepare()
    assert not pending._add_parent_job(_Node("late"))


def test_failing_cascades_to_every_parent_that_needs_it_and_is_idempotent():
    b = _Node("b")
    a = _Node("a", [b])
    c = _Node("c", [b])
    _link(a)
    _link(c)

    assert b._fail("boom")
    assert not b._fail("boom again")

    assert b.is_failed()
    assert b.error() == "boom"
    assert a.is_failed()
    assert a.error() == "dependency b failed"
    assert c.is_failed()
