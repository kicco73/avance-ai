from __future__ import annotations

import threading
import time

import pytest

from conftest import NullBroadcaster
from jobs import CancelableJob, ThrottledJobQueue
from jobs import throttled_job_queue as throttled_job_queue_module

pytestmark = pytest.mark.contract


class _FakeTime:
    """Stand-in for the stdlib time module: sleeping advances the clock
    instead of blocking, so a test exercising a full 60-second throttle
    window runs instantly."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.now = 0.0

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


@pytest.fixture
def fake_time(monkeypatch):
    fake = _FakeTime()
    monkeypatch.setattr(throttled_job_queue_module, "time", fake)
    return fake


class _TimestampedJob(CancelableJob):
    def __init__(self, key: str, log: list[float], fake_time: _FakeTime, is_background: bool = True) -> None:
        super().__init__(key=key, username="test")
        self._log = log
        self._fake_time = fake_time
        self._is_background = is_background

    def _prepare(self) -> tuple[int, list[CancelableJob]]:
        return 1, []

    @property
    def is_background(self) -> bool:
        return self._is_background

    @property
    def result(self) -> str | None:
        return "done"

    async def _run_next_step(self) -> None:
        self._log.append(self._fake_time.now)


def _wait_until(predicate, timeout=3.0, interval=0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_min_job_interval_ms_is_respected_between_consecutive_jobs(fake_time):
    log: list[float] = []
    job_queue = ThrottledJobQueue(
        max_concurrent=1,
        broadcaster=NullBroadcaster(),
        max_jobs_per_minute=1000,
        min_job_interval_ms=100,
    )
    jobs = [_TimestampedJob(f"job-{i}", log, fake_time) for i in range(3)]

    for job in jobs:
        job_queue.submit(job)

    assert _wait_until(lambda: all(job.is_done() for job in jobs))
    assert len(log) == 3
    for previous, current in zip(log, log[1:]):
        assert current - previous >= 0.1


def test_max_jobs_per_minute_forces_a_wait_until_the_next_window(fake_time):
    log: list[float] = []
    job_queue = ThrottledJobQueue(
        max_concurrent=1,
        broadcaster=NullBroadcaster(),
        max_jobs_per_minute=2,
        min_job_interval_ms=0,
    )
    jobs = [_TimestampedJob(f"job-{i}", log, fake_time) for i in range(3)]

    for job in jobs:
        job_queue.submit(job)

    assert _wait_until(lambda: all(job.is_done() for job in jobs))
    assert len(log) == 3
    # First two jobs consume the per-minute budget immediately...
    assert log[1] - log[0] < 1
    # ...the third must wait for the next 60-second window to open.
    assert log[2] - log[0] >= 60


def test_max_jobs_per_minute_is_a_true_sliding_window_across_the_boundary(fake_time):
    log: list[float] = []
    job_queue = ThrottledJobQueue(
        max_concurrent=1,
        broadcaster=NullBroadcaster(),
        max_jobs_per_minute=2,
        min_job_interval_ms=0,
    )

    # Run the first two jobs right at the tail end of the queue's first
    # 60-second window...
    fake_time.sleep(59.99)
    tail_jobs = [_TimestampedJob(f"tail-{i}", log, fake_time) for i in range(2)]
    for job in tail_jobs:
        job_queue.submit(job)
    assert _wait_until(lambda: all(job.is_done() for job in tail_jobs))

    # ...then nudge the clock just past that window boundary and submit one
    # more. A fixed window resets its count exactly here and would let this
    # job through immediately, producing 3 jobs inside a ~20ms span even
    # though the limit is 2 per minute. A true sliding window must still
    # count the first two as "within the last 60 seconds" and make this one
    # wait out the rest of that window.
    fake_time.sleep(0.02)
    late_job = _TimestampedJob("late", log, fake_time)
    job_queue.submit(late_job)
    assert _wait_until(lambda: late_job.is_done())

    assert len(log) == 3
    assert log[2] - log[0] >= 60


def test_non_background_jobs_bypass_the_throttle(fake_time):
    """An aggregation job (is_background=False) becomes runnable only once
    every dependency it waits on has finished — it does no AI-call work of
    its own, so it must not also wait out the same per-minute/interval
    budget meant to protect the AI provider from real background (batch
    replay) steps."""
    log: list[float] = []
    job_queue = ThrottledJobQueue(
        max_concurrent=1,
        broadcaster=NullBroadcaster(),
        max_jobs_per_minute=1,
        min_job_interval_ms=100_000,
    )
    background_job = _TimestampedJob("background", log, fake_time, is_background=True)
    job_queue.submit(background_job)
    assert _wait_until(lambda: background_job.is_done())

    interactive_jobs = [
        _TimestampedJob(f"interactive-{i}", log, fake_time, is_background=False) for i in range(5)
    ]
    for job in interactive_jobs:
        job_queue.submit(job)
    assert _wait_until(lambda: all(job.is_done() for job in interactive_jobs))

    assert len(log) == 6
    # Every non-background job ran right after the throttled background
    # one, none of them waiting out max_jobs_per_minute=1 or the 100s
    # min_job_interval_ms — both of which would otherwise force each of
    # the 5 interactive jobs onto its own new 60s+ window.
    assert log[-1] - log[0] < 1


class _BlockingTime:
    """_FakeTime's controlled clock, but sleep() also blocks on a real
    threading.Event until release() is called — deterministically parks
    a worker mid-throttle so a test can observe is_paused() during that
    exact window, with no real wall-clock wait and no flakiness (plain
    _FakeTime's sleep() returns instantly, leaving nothing to observe).
    Advancing the clock only once actually released mirrors real time:
    the throttle loop's next wait_seconds recompute sees the full
    duration as having elapsed, so it doesn't just spin back into another
    sleep()."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.now = 0.0
        self._release = threading.Event()
        self.entered_sleep = threading.Event()

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        self.entered_sleep.set()
        self._release.wait()
        with self._lock:
            self.now += seconds

    def release(self) -> None:
        self._release.set()


class _NoOpJob(CancelableJob):
    def __init__(self, key: str) -> None:
        super().__init__(key=key, username="test")

    def _prepare(self) -> tuple[int, list[CancelableJob]]:
        return 1, []

    @property
    def result(self) -> str | None:
        return "done"

    async def _run_next_step(self) -> None:
        pass


class _RecordingBroadcaster:
    """Captures every push() verbatim — NullBroadcaster drops them, so
    it can't tell us what queue_status a given broadcast actually
    carried."""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def push(self, username: str, message: dict) -> None:
        with self._lock:
            self.pushed.append((username, message))

    def statuses_for(self, key: str) -> list[str]:
        with self._lock:
            return [message["queue_status"] for _, message in self.pushed if message["key"] == key]


def test_broadcasts_paused_while_asleep_then_running_once_released(monkeypatch):
    blocking_time = _BlockingTime()
    monkeypatch.setattr(throttled_job_queue_module, "time", blocking_time)
    broadcaster = _RecordingBroadcaster()

    job_queue = ThrottledJobQueue(
        max_concurrent=1, broadcaster=broadcaster,
        max_jobs_per_minute=1, min_job_interval_ms=0,
    )
    first = _NoOpJob("first")
    job_queue.submit(first)
    assert _wait_until(lambda: first.is_done())
    assert "paused" not in broadcaster.statuses_for("first")

    # The per-minute budget (1) is already spent — this one must now
    # wait inside _throttle(), giving us a real, observable pause window.
    second = _NoOpJob("second")
    job_queue.submit(second)

    assert blocking_time.entered_sleep.wait(timeout=3.0)
    assert _wait_until(lambda: "paused" in broadcaster.statuses_for("second"))
    # Not yet "running" — the whole point of fixing _dequeue_and_notify's
    # ordering was that "running" must never precede the pause it's
    # waiting out.
    assert "running" not in broadcaster.statuses_for("second")

    blocking_time.release()
    assert _wait_until(lambda: second.is_done())
    assert "running" in broadcaster.statuses_for("second")
