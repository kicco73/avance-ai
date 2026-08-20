from __future__ import annotations

import threading
import time

import pytest

from jobs import InMemoryJobSink, JobQueue

pytestmark = pytest.mark.contract


def _wait_until(predicate, timeout=2.0, interval=0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_submit_returns_immediately_and_the_job_runs_to_completion():
    sink = InMemoryJobSink()
    started = threading.Event()

    async def work(on_progress):
        started.set()
        on_progress(1)
        return None, "done"

    job_queue = JobQueue(sink, max_concurrent=1)
    job_id = job_queue.submit("some_kind", reference_id=None, total=1, work=work)

    assert started.wait(timeout=2.0)
    assert _wait_until(lambda: sink.get(job_id)["status"] == "completed")

    job = sink.get(job_id)
    assert job["result"] == "done"
    assert job["progress_current"] == 1
    assert job["error"] is None


def test_a_raising_job_is_marked_failed_with_the_exception_message():
    sink = InMemoryJobSink()

    async def work(on_progress):
        raise ValueError("boom")

    job_queue = JobQueue(sink, max_concurrent=1)
    job_id = job_queue.submit("k", None, 1, work)

    assert _wait_until(lambda: sink.get(job_id)["status"] == "failed")
    assert sink.get(job_id)["error"] == "boom"


def test_progress_callback_updates_the_sink_while_running():
    sink = InMemoryJobSink()
    saw_progress = threading.Event()

    async def work(on_progress):
        on_progress(5)
        saw_progress.set()
        return None, None

    job_queue = JobQueue(sink, max_concurrent=1)
    job_queue.submit("k", None, 10, work)

    assert saw_progress.wait(timeout=2.0)


def test_jobs_beyond_pool_size_stay_pending_until_a_worker_frees_up():
    sink = InMemoryJobSink()
    release_first = threading.Event()
    first_started = threading.Event()

    async def blocking_work(on_progress):
        first_started.set()
        # Blocking here is exactly what a real synchronous db write would
        # do — fine on this job's own dedicated thread.
        release_first.wait(timeout=2.0)
        return None, None

    async def quick_work(on_progress):
        return None, None

    job_queue = JobQueue(sink, max_concurrent=1)
    first_id = job_queue.submit("k", None, 1, blocking_work)
    second_id = job_queue.submit("k", None, 1, quick_work)

    assert first_started.wait(timeout=2.0)
    # The pool's single worker is already busy with the first job — the
    # second must still be queued, never rejected.
    assert sink.get(second_id)["status"] == "pending"

    release_first.set()

    assert _wait_until(lambda: sink.get(second_id)["status"] == "completed")
    assert sink.get(first_id)["status"] == "completed"


def test_two_queues_never_share_worker_pools():
    sink_a = InMemoryJobSink()
    sink_b = InMemoryJobSink()
    queue_a = JobQueue(sink_a, max_concurrent=1)
    queue_b = JobQueue(sink_b, max_concurrent=1)

    block = threading.Event()
    started_a = threading.Event()

    async def blocking_work(on_progress):
        started_a.set()
        block.wait(timeout=2.0)
        return None, None

    async def quick_work(on_progress):
        return None, None

    job_a = queue_a.submit("k", None, 1, blocking_work)
    assert started_a.wait(timeout=2.0)

    job_b = queue_b.submit("k", None, 1, quick_work)
    assert _wait_until(lambda: sink_b.get(job_b)["status"] == "completed")
    # queue_a's only worker is still blocked on job_a — proves job_b never
    # had to wait for it, i.e. the two pools are genuinely independent.
    assert sink_a.get(job_a)["status"] == "running"

    block.set()
    assert _wait_until(lambda: sink_a.get(job_a)["status"] == "completed")
