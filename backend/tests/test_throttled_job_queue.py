from __future__ import annotations

import threading
import time

import pytest

from conftest import NullBroadcaster
from jobs import Job, ThrottledJobQueue
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


class _TimestampedJob(Job):
    def __init__(self, key: str, log: list[float], fake_time: _FakeTime) -> None:
        super().__init__(key=key, username="test")
        self._log = log
        self._fake_time = fake_time

    def _prepare(self) -> tuple[int, list[Job]]:
        return 1, []

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
