from __future__ import annotations

import asyncio

import pytest

from db import Db
from jobs import Job
from testing.cache import TestCache

pytestmark = pytest.mark.contract


class _FakeJob(Job):
    """A stand-in for a TestReplayJob, driven through the real
    prepare()/run_next_step() so its terminal state comes from the same
    path any other job's does — never poked into Job's own state directly."""

    def __init__(self, should_fail: bool) -> None:
        super().__init__(key="fake", username="test")
        self._should_fail = should_fail

    def _prepare(self) -> tuple[int, tuple[Job, ...]]:
        return 1, ()

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        if self._should_fail:
            raise RuntimeError("boom")


def _make_dead_run(db: Db, session_id: int) -> dict:
    return db.create_test(None, "proj", session_id, "batch", 1, 0, {})


def _run_to_terminal(job: Job) -> Job:
    job.prepare()
    try:
        asyncio.run(job.run_next_step())
    except Exception:
        pass
    return job


def test_find_treats_a_failed_jobs_run_as_a_cache_miss_and_forgets_it(db: Db):
    db.ensure_project("proj")
    session_id = db.create_chat_session("user", "proj", revision=0)
    run = _make_dead_run(db, session_id)

    cache = TestCache(db)
    with cache.locked():
        cache.track(run["id"], _run_to_terminal(_FakeJob(should_fail=True)))
        found = cache.find(session_id, "batch", 1, 0)

    assert found is None
    # Not just a cache miss — the dead job must no longer be pinned in
    # memory, otherwise every retry leaks another orphaned reference.
    assert cache.live_job_for(run["id"]) is None


def test_find_still_waits_for_a_job_that_is_genuinely_still_running(db: Db):
    db.ensure_project("proj")
    session_id = db.create_chat_session("user", "proj", revision=0)
    run = _make_dead_run(db, session_id)

    cache = TestCache(db)
    job = _FakeJob(should_fail=False)
    job.prepare()  # prepared but never run — genuinely still in flight
    with cache.locked():
        cache.track(run["id"], job)
        found = cache.find(session_id, "batch", 1, 0)

    assert found is not None and found["id"] == run["id"]
    assert cache.live_job_for(run["id"]) is not None


def test_find_treats_a_row_with_no_tracked_job_at_all_as_a_cache_miss(db: Db):
    """No job was ever tracked for this run (e.g. the server restarted
    mid-run) — still a dead attempt, still retryable."""
    db.ensure_project("proj")
    session_id = db.create_chat_session("user", "proj", revision=0)
    _make_dead_run(db, session_id)

    cache = TestCache(db)
    with cache.locked():
        found = cache.find(session_id, "batch", 1, 0)

    assert found is None


def test_find_returns_a_completed_run_regardless_of_tracked_job_state(db: Db):
    db.ensure_project("proj")
    session_id = db.create_chat_session("user", "proj", revision=0)
    run = _make_dead_run(db, session_id)
    db.set_test_results(run["id"], "[]")

    cache = TestCache(db)
    with cache.locked():
        found = cache.find(session_id, "batch", 1, 0)

    assert found is not None and found["id"] == run["id"]
