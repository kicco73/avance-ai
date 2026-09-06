"""Regression coverage for the "reconnecting to Test shows stale/idle
state" problem: get_test_status serves LastStatusBroadcaster's own
recorded last-message-per-key snapshot directly — one broadcaster records
state and forwards to the /ws/notifications-facing one, instead of a
second, separately re-derived computation (job.status()/the DB) that
could disagree with it.
"""
from __future__ import annotations

import threading
import time

import pytest

from jobs import CancelableJob
from testing.jobs import AllStatesAggregationJob
from testing.jobs.state_aggregation_job import StateAggregationJob
from testing.last_status_broadcaster import LastStatusBroadcaster

pytestmark = pytest.mark.contract


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _FakeInner:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, dict]] = []

    def connect(self, username):
        return object()

    def disconnect(self, username, connection):
        pass

    def push(self, username, message):
        self.pushed.append((username, message))


def test_last_status_broadcaster_records_and_forwards_every_push():
    inner = _FakeInner()
    broadcaster = LastStatusBroadcaster(inner)

    broadcaster.push("alice", {"key": "batch:sessions-branch", "job_status": "running", "queue_status": "running"})

    assert broadcaster.last_status("batch:sessions-branch")["job_status"] == "running"
    assert inner.pushed == [("alice", {"key": "batch:sessions-branch", "job_status": "running", "queue_status": "running"})]


def test_last_status_broadcaster_keeps_only_the_latest_message_per_key():
    inner = _FakeInner()
    broadcaster = LastStatusBroadcaster(inner)

    broadcaster.push("alice", {"key": "batch:root", "job_status": "running", "queue_status": "running"})
    broadcaster.push("alice", {"key": "batch:root", "job_status": "completed", "queue_status": "exited"})

    assert broadcaster.last_status("batch:root")["job_status"] == "completed"
    assert broadcaster.snapshot() == [{"key": "batch:root", "job_status": "completed", "queue_status": "exited"}]


def test_last_status_broadcaster_clear_and_forget():
    inner = _FakeInner()
    broadcaster = LastStatusBroadcaster(inner)
    broadcaster.push("alice", {"key": "batch:root", "job_status": "completed", "queue_status": "exited"})
    broadcaster.push("alice", {"key": "batch:sessions-branch", "job_status": "completed", "queue_status": "exited"})

    broadcaster.forget("batch:root")
    assert broadcaster.last_status("batch:root") is None
    assert broadcaster.last_status("batch:sessions-branch") is not None

    broadcaster.clear()
    assert broadcaster.snapshot() == []


def test_get_test_status_returns_the_broadcaster_snapshot(client, hello_project):
    """A job that already finished before anyone asked must still be
    visible in the very next snapshot read — the whole point of
    LastStatusBroadcaster recording rather than only ever forwarding."""
    test_service = client.app.state.test_service
    test_service._status_broadcaster.push(
        "user", {"key": "batch:root", "job_status": "completed", "queue_status": "exited", "error": None},
    )

    events = client.get(f"/api/projects/{hello_project}/test-status").json()["events"]

    assert any(m.get("key") == "batch:root" and m.get("job_status") == "completed" for m in events), events


class _BlockingCancelableJob(CancelableJob):
    def __init__(self, key: str, started: threading.Event, release: threading.Event) -> None:
        super().__init__(key=key, username="test")
        self._started = started
        self._release = release

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        self._started.set()
        self._release.wait(timeout=2.0)


def test_root_shows_running_via_the_broadcaster_even_though_it_never_persists(client, hello_project):
    """RootAggregationJob never writes a TestAggregateResult of its own
    (see its class) — the only way to ever know it's running is the
    queue's own last broadcast, so it must never be silently dropped."""
    test_service = client.app.state.test_service
    started = threading.Event()
    release = threading.Event()
    job = _BlockingCancelableJob("batch:root", started, release)
    test_service._submit(job)
    assert started.wait(timeout=2.0)

    last = test_service._status_broadcaster.last_status("batch:root")
    assert last["job_status"] == "running"

    release.set()


def test_an_individual_state_job_reports_its_own_running_status_via_all_states(monkeypatch, client, hello_project):
    """AllStatesAggregationJob constructs one StateAggregationJob per
    state as a plain dependency, never separately submitted through
    TestService._submit() — job_queue.py still broadcasts for it directly
    (see JobQueue.submit's own recursion), so the broadcaster records its
    real status regardless of whether TestService tracks it anywhere."""
    started = threading.Event()
    release = threading.Event()
    original_compute = StateAggregationJob._compute

    async def blocking_compute(self):
        started.set()
        release.wait(timeout=2.0)
        return await original_compute(self)

    monkeypatch.setattr(StateAggregationJob, "_compute", blocking_compute)

    test_service = client.app.state.test_service
    job = AllStatesAggregationJob(test_service, hello_project, "batch", {"Hello": []})
    test_service._submit(job)
    assert started.wait(timeout=2.0)

    last = test_service._status_broadcaster.last_status("batch:state:Hello")
    assert last["job_status"] == "running"
    assert last["queue_status"] == "running"

    release.set()


def test_reset_cache_clears_the_broadcasters_recorded_state(client, hello_project):
    """reset_cache() deletes the persisted results but must also drop the
    broadcaster's recorded last-message state — otherwise an aggregate
    that completed before the reset keeps reporting 'completed' (nothing
    ever tells the recorded state it's now stale) even though its data
    was just deleted."""
    session = client.get("/api/chat/session").json()
    session_id = session["id"]
    client.post(f"/api/chat/sessions/{session_id}/messages", json={"message": "hi"})
    client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True})

    response = client.post(f"/api/projects/{hello_project}/sessions/test", json={"strategy": "turn_by_turn"})
    assert response.status_code == 200, response.text

    test_service = client.app.state.test_service

    def sessions_completed():
        last = test_service._status_broadcaster.last_status("turn_by_turn:sessions-branch")
        return last is not None and last["job_status"] == "completed"

    assert _wait_until(sessions_completed)

    response = client.delete(f"/api/projects/{hello_project}/tests")
    assert response.status_code == 200, response.text

    assert test_service._status_broadcaster.last_status("turn_by_turn:sessions-branch") is None
