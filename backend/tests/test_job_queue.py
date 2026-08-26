from __future__ import annotations

import threading
import time

import pytest

from conftest import NullBroadcaster
from jobs import Job, JobQueue

pytestmark = pytest.mark.contract


def _queue(max_concurrent: int = 1) -> JobQueue:
    return JobQueue(max_concurrent=max_concurrent, broadcaster=NullBroadcaster())


def _wait_until(predicate, timeout=2.0, interval=0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _QuickJob(Job):
    def __init__(self, started: threading.Event | None = None) -> None:
        super().__init__(key="quick", username="test")
        self._started = started
        self._result: str | None = None

    def _prepare(self) -> tuple[int, list[Job]]:
        return 1, []

    @property
    def result(self) -> str | None:
        return self._result

    async def _run_next_step(self) -> None:
        if self._started is not None:
            self._started.set()
        self._result = "done"


class _RaisingJob(Job):
    def __init__(self, message: str) -> None:
        super().__init__(key="raising", username="test")
        self._message = message

    def _prepare(self) -> tuple[int, list[Job]]:
        return 1, []

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        raise ValueError(self._message)


class _BlockingJob(Job):
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        super().__init__(key="blocking", username="test")
        self._started = started
        self._release = release

    def _prepare(self) -> tuple[int, list[Job]]:
        return 1, []

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        self._started.set()
        self._release.wait(timeout=2.0)


def test_submit_returns_immediately_and_the_job_runs_to_completion():
    started = threading.Event()
    job_queue = _queue()
    job = _QuickJob(started)

    job_queue.submit(job)

    assert started.wait(timeout=2.0)
    assert _wait_until(lambda: job.is_done())
    assert job.result == "done"
    assert not job.is_failed()


def test_a_raising_job_is_marked_failed():
    job_queue = _queue()
    job = _RaisingJob("boom")

    job_queue.submit(job)

    assert _wait_until(lambda: job.is_failed())


def test_jobs_beyond_pool_size_wait_until_a_worker_frees_up():
    started = threading.Event()
    release = threading.Event()
    job_queue = _queue()
    first = _BlockingJob(started, release)
    second = _QuickJob()

    job_queue.submit(first)
    job_queue.submit(second)

    assert started.wait(timeout=2.0)
    # The pool's single worker is already busy with the first job — the
    # second must still be waiting, never rejected.
    assert not second.is_done()

    release.set()

    assert _wait_until(lambda: second.is_done())
    assert _wait_until(lambda: first.is_done())


def test_two_queues_never_share_worker_pools():
    started_a = threading.Event()
    block = threading.Event()
    queue_a = _queue()
    queue_b = _queue()
    job_a = _BlockingJob(started_a, block)
    job_b = _QuickJob()

    queue_a.submit(job_a)
    assert started_a.wait(timeout=2.0)

    queue_b.submit(job_b)
    assert _wait_until(lambda: job_b.is_done())
    # queue_a's only worker is still blocked on job_a — proves job_b never
    # had to wait for it, i.e. the two pools are genuinely independent.
    assert not job_a.is_done()

    block.set()
    assert _wait_until(lambda: job_a.is_done())
