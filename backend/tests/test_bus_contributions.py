"""A contribution point assembles the thing its caller is about to act
on, so a contributor that fails is never logged past.

The loader a build reads every automaton through, the router it answers
requests with, the services it wires against: each arrives through
bus.collect, and each has exactly one chance to be right. Swallowing a
failure here leaves a system that runs, answers, and is wrong — which is
what a product whose packaged loader raised used to do, falling back to
the database loader without a word.
"""
from __future__ import annotations

import pytest

from system import bus

pytestmark = pytest.mark.contract

POINT = "test.point"


@pytest.fixture(autouse=True)
def clean_bus():
    bus._reset_for_tests()
    yield
    bus._reset_for_tests()


def test_a_contributor_that_raises_takes_the_collect_down_with_it():
    bus.contribute(POINT, lambda target: target.update({"first": True}))
    bus.contribute(POINT, _explode)

    with pytest.raises(RuntimeError, match="this skill is broken"):
        bus.collect(POINT, {})


def test_the_failure_is_the_skill_s_own_exception_not_a_wrapper():
    """Whoever is looking at the boot log needs the skill's own traceback,
    not one of the bus's making."""
    bus.contribute(POINT, _explode)

    with pytest.raises(RuntimeError) as raised:
        bus.collect(POINT, {})

    assert raised.value.__cause__ is None
    assert str(raised.value) == "this skill is broken"


def test_a_later_contributor_never_runs_once_one_has_failed():
    """Half an assembled target is not a target. The caller gets nothing
    to act on rather than something plausible."""
    ran = []
    bus.contribute(POINT, _explode)
    bus.contribute(POINT, lambda target: ran.append("second"))

    with pytest.raises(RuntimeError):
        bus.collect(POINT, {})

    assert ran == []


def test_a_point_nobody_contributed_to_is_not_a_failure():
    assert bus.collect(POINT, {"untouched": True}) == {"untouched": True}


def _explode(target):
    raise RuntimeError("this skill is broken")
