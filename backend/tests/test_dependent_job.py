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


def test_children_and_parents_reflect_the_prepared_topology():
    b = _Node("b")
    a = _Node("a", [b])
    _link(a)

    assert a.children == (b,)
    assert b.parents == (a,)


def test_children_registered_is_true_immediately_for_a_job_with_no_dependencies():
    a = _Node("a")
    a.prepare()

    assert a._children_registered()


def test_children_registered_is_false_while_a_dependency_is_still_outstanding():
    b = _Node("b")
    a = _Node("a", [b])
    a.prepare()

    assert not a._children_registered()


def test_dependency_resolved_before_children_registered_defers_readiness_to_registration():
    """Mirrors a dependency finishing faster than submit()'s own recursive
    registration loop: the removal happens immediately, but readiness
    must wait for _children_registered() to confirm no more are coming."""
    b = _Node("b")
    a = _Node("a", [b])
    a.prepare()

    assert not a._dependency_resolved(b)
    assert a._children_registered()


def test_children_registered_before_dependency_resolved_defers_readiness_to_resolution():
    b = _Node("b")
    a = _Node("a", [b])
    a.prepare()

    assert not a._children_registered()
    assert a._dependency_resolved(b)


def test_dependency_resolved_is_a_one_shot_signal_for_the_same_dependency():
    b = _Node("b")
    a = _Node("a", [b])
    a.prepare()
    a._children_registered()

    assert a._dependency_resolved(b)
    assert not a._dependency_resolved(b)


def test_dependency_resolved_is_false_for_a_dependency_it_never_had():
    a = _Node("a")
    stranger = _Node("stranger")
    a.prepare()

    assert not a._dependency_resolved(stranger)


async def test_add_parent_job_reports_true_when_the_dependency_is_already_terminal():
    b = _Node("b")
    b.prepare()
    await b.run_next_step()
    assert b.is_done()

    late_parent = _Node("late")
    assert b._add_parent_job(late_parent)


def test_add_parent_job_reports_false_while_the_dependency_is_still_pending():
    b = _Node("b")
    b.prepare()

    late_parent = _Node("late")
    assert not b._add_parent_job(late_parent)


def test_fail_cascades_to_every_parent():
    b = _Node("b")
    a = _Node("a", [b])
    _link(a)

    b._fail("boom")

    assert b.is_failed()
    assert b.error() == "boom"
    assert a.is_failed()
    assert a.error() == "dependency b failed"


def test_fail_is_idempotent():
    b = _Node("b")
    _link(b)

    assert b._fail("boom")
    assert not b._fail("boom again")
    assert b.error() == "boom"


def test_a_failed_shared_dependency_fails_every_root_that_needs_it():
    b = _Node("b")
    a = _Node("a", [b])
    c = _Node("c", [b])
    _link(a)
    _link(c)

    b._fail("out of credits")

    assert b.is_failed()
    assert a.is_failed()
    assert c.is_failed()
