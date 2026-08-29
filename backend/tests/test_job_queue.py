from __future__ import annotations

import threading
import time

import pytest

from conftest import NullBroadcaster
from jobs import CancelableJob, JobQueue
from try_again_error import TryAgainError

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


class _QuickJob(CancelableJob):
    def __init__(self, started: threading.Event | None = None) -> None:
        super().__init__(key="quick", username="test")
        self._started = started
        self._result: str | None = None

    def _prepare(self) -> tuple[int, list[CancelableJob]]:
        return 1, []

    @property
    def result(self) -> str | None:
        return self._result

    async def _run_next_step(self) -> None:
        if self._started is not None:
            self._started.set()
        self._result = "done"


class _RaisingJob(CancelableJob):
    def __init__(self, message: str) -> None:
        super().__init__(key="raising", username="test")
        self._message = message

    def _prepare(self) -> tuple[int, list[CancelableJob]]:
        return 1, []

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        raise ValueError(self._message)


class _WaiterJob(CancelableJob):
    """Stands in for an aggregation job: its own _run_next_step would
    otherwise always succeed, regardless of whether the dependency it
    waited on failed — mirrors an aggregation reading whatever partial
    data is left behind, with no exception of its own."""

    def __init__(self, dependency: CancelableJob, key: str = "waiter") -> None:
        super().__init__(key=key, username="test")
        self._dependency = dependency
        self._result: str | None = None

    def _prepare(self) -> tuple[int, list[CancelableJob]]:
        return 1, [self._dependency]

    @property
    def result(self) -> str | None:
        return self._result

    async def _run_next_step(self) -> None:
        self._result = "done"


class _FlakyJob(CancelableJob):
    """Raises TryAgainError on its first `fail_times` attempts, then
    succeeds — records its own key into `order` (if given) only on the
    attempt that actually succeeds."""

    def __init__(self, background: bool, fail_times: int, key: str, order: list[str] | None = None) -> None:
        super().__init__(key=key, username="test")
        self._background = background
        self._fail_times = fail_times
        self._attempts = 0
        self._order = order
        self._result: str | None = None

    def _prepare(self) -> tuple[int, list[CancelableJob]]:
        return 1, []

    @property
    def is_background(self) -> bool:
        return self._background

    @property
    def result(self) -> str | None:
        return self._result

    async def _run_next_step(self) -> None:
        self._attempts += 1
        if self._attempts <= self._fail_times:
            raise TryAgainError(f"attempt {self._attempts}")
        self._result = "done"
        if self._order is not None:
            self._order.append(self.key)


class _BlockingJob(CancelableJob):
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        super().__init__(key="blocking", username="test")
        self._started = started
        self._release = release

    def _prepare(self) -> tuple[int, list[CancelableJob]]:
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


def test_a_failed_shared_dependency_fails_every_waiter():
    """Two independent 'branches' (e.g. a state aggregation and a users
    aggregation) both depend on the very same session job, which fails
    (e.g. the AI provider ran out of credits). Both waiters must end up
    failed themselves, even though each one's own _run_next_step never
    raises on its own."""
    job_queue = _queue(max_concurrent=2)
    shared_dependency = _RaisingJob("out of credits")
    first_waiter = _WaiterJob(shared_dependency, key="first")
    second_waiter = _WaiterJob(shared_dependency, key="second")

    job_queue.submit(first_waiter)
    job_queue.submit(second_waiter)

    assert _wait_until(lambda: shared_dependency.is_failed())
    assert _wait_until(lambda: first_waiter.is_failed())
    assert _wait_until(lambda: second_waiter.is_failed())
    assert first_waiter.result is None
    assert second_waiter.result is None


def test_a_dependency_that_already_finished_before_a_second_waiter_needs_it_still_runs_that_waiter():
    """Mirrors 'root': two branches resolve the same underlying session
    job, but the first branch's copy can finish (and be forgotten) before
    the second branch even gets to submit it — its own _forget() already
    ran once, with nobody yet registered to be told. The second waiter
    must still run to completion, not crash on a second prepare() nor
    hang forever waiting for a notification that already fired."""
    job_queue = _queue(max_concurrent=2)
    already_finished_dependency = _QuickJob()

    job_queue.submit(already_finished_dependency)
    assert _wait_until(lambda: already_finished_dependency.is_done())

    late_waiter = _WaiterJob(already_finished_dependency, key="late")
    job_queue.submit(late_waiter)

    assert _wait_until(lambda: late_waiter.is_done())
    assert late_waiter.result == "done"
    assert not late_waiter.is_failed()


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


async def test_wait_for_returns_once_the_job_completes():
    job_queue = _queue()
    job = _QuickJob()

    job_queue.submit(job)
    await job_queue.wait_for(job)

    assert job.is_done()
    assert job.result == "done"


async def test_wait_for_returns_once_the_job_fails():
    job_queue = _queue()
    job = _RaisingJob("boom")

    job_queue.submit(job)
    await job_queue.wait_for(job)

    assert job.is_failed()


async def test_wait_for_returns_immediately_when_the_job_is_already_terminal():
    job_queue = _queue()
    job = _QuickJob()

    job_queue.submit(job)
    assert _wait_until(lambda: job.is_done())

    await job_queue.wait_for(job)

    assert job.is_done()


def test_try_again_error_requeues_instead_of_failing_the_job():
    """A transient failure (e.g. the AI provider cascade being exhausted)
    must not kill the job outright the way any other exception does — it
    gets another turn instead, and the job only reaches a terminal state
    once it actually succeeds."""
    job_queue = _queue()
    job = _FlakyJob(background=True, fail_times=2, key="flaky")

    job_queue.submit(job)

    assert _wait_until(lambda: job.is_done())
    assert not job.is_failed()
    assert job.result == "done"


def test_try_again_error_becomes_a_permanent_failure_after_max_retries():
    """A transient failure that never clears (e.g. the AI provider staying
    down) must not retry forever — after Job.MAX_RETRIES attempts it turns
    into an ordinary permanent failure instead of requeuing again."""
    job_queue = _queue()
    job = _FlakyJob(background=True, fail_times=10, key="flaky")

    job_queue.submit(job)

    assert _wait_until(lambda: job.is_failed())
    assert not job.is_done()
    assert job.error() == "attempt 3"


def test_try_again_error_forces_tail_even_for_a_priority_job():
    """A non-background job normally jumps straight to the head of the
    deque on resubmission, preempting background work. A TryAgainError
    retry must never get that treatment — it goes to the tail like
    anything else, so a background job already queued behind it still
    gets its turn first, rather than being starved by an interactive job
    stuck retrying the same transient failure."""
    started = threading.Event()
    release = threading.Event()
    job_queue = _queue(max_concurrent=1)
    completion_order: list[str] = []

    blocker = _BlockingJob(started, release)
    job_queue.submit(blocker)
    assert started.wait(timeout=2.0)

    flaky = _FlakyJob(background=False, fail_times=1, key="flaky", order=completion_order)
    background_job = _FlakyJob(background=True, fail_times=0, key="background", order=completion_order)
    job_queue.submit(flaky)
    job_queue.submit(background_job)

    release.set()

    assert _wait_until(lambda: flaky.is_done() and background_job.is_done())
    assert completion_order == ["background", "flaky"]
