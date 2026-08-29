from __future__ import annotations

import threading
import time

import pytest

from session import Session
from testing.test_service import PooledAggregationJob, TestService

pytestmark = pytest.mark.contract


def _make_labeled_session(client, app_db, project_name, username):
    Session().user = username
    app_db.set_active_project_name(project_name, username)
    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})
    client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    Session().user = "user"
    return session["id"]


def test_resolve_or_construct_session_run_serializes_racing_callers(monkeypatch, client, app_db, hello_project):
    """Two callers resolving a dependency on the same underlying session
    replay used to be able to race: _resolve_or_construct_session_run's own
    list_runs() check ran unlocked, so a second caller could see "nothing
    running yet" before the first had committed its row, fall through to
    _construct_run, and get back job=None (since the row already existed by
    the time it got there) instead of the first caller's live job — silently
    dropping the wait dependency on an in-flight run. Forces the
    interleaving deterministically instead of relying on scheduling luck.
    """
    session_id = _make_labeled_session(client, app_db, hello_project, "alice")
    test_service = client.app.state.test_service

    entered = threading.Event()
    release = threading.Event()
    calls: list[int] = []
    original_list_runs = TestService.list_runs

    def instrumented_list_runs(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(timeout=5.0), "test itself never released the first caller"
        return original_list_runs(self, *args, **kwargs)

    monkeypatch.setattr(TestService, "list_runs", instrumented_list_runs)

    job_a = PooledAggregationJob(test_service, hello_project, 'sessions', None, 'turn_by_turn', [session_id])
    job_b = PooledAggregationJob(test_service, hello_project, 'sessions', None, 'turn_by_turn', [session_id])
    results = {}

    def resolve(name, job):
        Session().user = "user"
        results[name] = job._resolve_or_construct_session_run(session_id)

    first = threading.Thread(target=resolve, args=("first", job_a))
    first.start()
    assert entered.wait(timeout=5.0)

    second = threading.Thread(target=resolve, args=("second", job_b))
    second.start()
    time.sleep(0.2)
    assert second.is_alive(), "second caller raced past the cache lock instead of blocking on it"

    release.set()
    first.join(timeout=5.0)
    second.join(timeout=5.0)

    run_id_a, job_ref_a = results["first"]
    run_id_b, job_ref_b = results["second"]
    assert run_id_a == run_id_b
    assert job_ref_a is not None and job_ref_b is not None
    assert job_ref_a is job_ref_b, "the two callers ended up with different Job objects for the same run"

    runs = [run for run in app_db.list_tests(hello_project, session_id) if run["strategy"] == "turn_by_turn"]
    assert len(runs) == 1, f"expected exactly one run for the shared session, got {len(runs)}"


def test_resolve_or_construct_session_run_treats_an_aborted_run_as_retryable(client, app_db, hello_project):
    """An aborted run must not be handed back as if it were still live —
    see TestService._status_for() and TestCache.find(): before those knew
    about is_aborted(), a cancelled TestReplayJob kept reporting 'running'
    forever, so the next click got back that same dead job instead of a
    fresh one, and nothing new ever ran."""
    session_id = _make_labeled_session(client, app_db, hello_project, "alice")
    test_service = client.app.state.test_service
    Session().user = "user"

    job = PooledAggregationJob(test_service, hello_project, 'sessions', None, 'turn_by_turn', [session_id])
    first_run_id, first_job = job._resolve_or_construct_session_run(session_id)
    assert first_job is not None
    first_job.prepare()
    first_job.cancel()

    _, second_job = job._resolve_or_construct_session_run(session_id)

    assert second_job is not None
    assert second_job is not first_job
    assert not second_job.is_aborted()
