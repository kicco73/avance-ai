from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
import simpleeval

from automaton.automaton import DeferredExpression, _OnEnterEval
from automaton.scope import EvaluationScope
from conftest import make_test_actuator_factory
from tracking.actuators.actuator_set import FakeActuatorSet, LiveActuatorSet

pytestmark = pytest.mark.contract


def _scope(names: dict) -> EvaluationScope:
    return EvaluationScope(names, automaton=None, state_key="a")


def _live_actuator_set(db) -> LiveActuatorSet:
    return make_test_actuator_factory(db).live(project_id="p")


def test_a_live_defer_refuses_anything_but_an_on_enter_lambda_and_a_real_datetime_scheduling_nothing(db):
    """No in-memory fallback exists: a plain callable has no source to
    hibernate, so it is refused rather than silently run once."""
    actuator = _live_actuator_set(db)

    with pytest.raises(TypeError, match="lambda"):
        actuator.defer(threading.Event().set, datetime.now(timezone.utc))

    act = _OnEnterEval(names=_scope({})).eval("lambda: 1")
    with pytest.raises(TypeError, match="datetime"):
        actuator.defer(act, "2030-01-01")

    assert db.list_tasks() == []


def test_a_fake_defer_never_schedules_anything_and_says_so():
    ran = threading.Event()

    result = FakeActuatorSet().defer(ran.set, datetime.now(timezone.utc))

    assert not ran.is_set()
    assert result is not None and "defer" in result


class _Recorder:
    def __init__(self) -> None:
        self.calls = []

    def record(self, value):
        self.calls.append(value)


def test_a_zero_argument_lambda_evaluates_to_a_deferred_expression_that_knows_its_own_source_and_scope_and_runs_on_call():
    recorder = _Recorder()
    scope = _scope({"recorder": recorder})

    act = _OnEnterEval(names=scope).eval("lambda: recorder.record(1 + 2)")

    assert isinstance(act, DeferredExpression)
    assert act.source == "recorder.record(1 + 2)"
    assert act.scope is scope
    assert recorder.calls == []
    act()
    assert recorder.calls == [3]


def test_on_enter_eval_rejects_a_lambda_with_arguments_and_a_plain_dict_scope():
    """A plain dict has no automaton/state behind it — nothing a deferred
    call could be hibernated with — so it is refused up front."""
    with pytest.raises(simpleeval.FeatureNotAvailable):
        _OnEnterEval(names=_scope({})).eval("lambda x: x")
    with pytest.raises(TypeError, match="EvaluationScope"):
        _OnEnterEval(names={})
