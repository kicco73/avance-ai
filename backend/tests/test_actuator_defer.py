from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import simpleeval

from automaton.automaton import _OnEnterEval
from conftest import NullBroadcaster
from jobs import JobQueue, ScheduledJobQueue
from tracking.actuators.actuator_set import FakeActuatorSet, LiveActuatorSet

pytestmark = pytest.mark.contract


def _wait_until(predicate, timeout=2.0, interval=0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_live_actuator_set_defer_runs_the_callable_once_due():
    job_queue = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
    scheduled_queue = ScheduledJobQueue(job_queue)
    actuator = LiveActuatorSet(notification_service=None, scheduled_job_queue=scheduled_queue, ws_adapter=None)
    ran = threading.Event()

    result = actuator.defer(ran.set, datetime.now(timezone.utc) - timedelta(seconds=1))

    assert result is None
    assert _wait_until(ran.is_set)


def test_fake_actuator_set_defer_never_schedules_anything():
    actuator = FakeActuatorSet()
    ran = threading.Event()

    result = actuator.defer(ran.set, datetime.now(timezone.utc))

    assert not ran.is_set()
    assert result is not None and "defer" in result


class _Recorder:
    def __init__(self) -> None:
        self.calls = []

    def record(self, value):
        self.calls.append(value)


def test_on_enter_eval_runs_a_zero_argument_lambda():
    recorder = _Recorder()
    evaluator = _OnEnterEval(names={"recorder": recorder})

    act = evaluator.eval("lambda: recorder.record(1)")
    assert recorder.calls == []
    act()
    assert recorder.calls == [1]


def test_on_enter_eval_rejects_a_lambda_with_arguments():
    evaluator = _OnEnterEval(names={})
    with pytest.raises(simpleeval.FeatureNotAvailable):
        evaluator.eval("lambda x: x")
