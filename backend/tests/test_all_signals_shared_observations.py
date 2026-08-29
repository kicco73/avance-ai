"""Regression test for AllSignalsAggregationJob: it fans out into one
SignalAggregationJob per project signal, all resolving the exact same
session/run ids. Before the fix, each one independently rebuilt the
(expensive, DB-round-trip-heavy) observation list for every run id it
touched, so a project with M signals and N sessions did M*N rebuilds of
data that's identical regardless of which signal is being read out of it —
see SignalAggregationJob's own observations_cache.
"""
from __future__ import annotations

import time

import pytest

from jobs import JobQueue
from testing.test_service import AllSignalsAggregationJob
from testing.jobs.base import _AggregationJob

pytestmark = pytest.mark.contract


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _make_completed_run(client, hello_project):
    session = client.get("/api/chat/session").json()
    session_id = session["id"]
    client.post(f"/api/chat/sessions/{session_id}/messages", json={"message": "hi"})
    client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True})

    leaf_run = client.post(
        f"/api/projects/{hello_project}/tests", json={"session_id": session_id, "strategy": "turn_by_turn"},
    ).json()
    deadline = time.monotonic() + 5.0
    run = client.get(f"/api/projects/{hello_project}/tests/{leaf_run['id']}").json()
    while time.monotonic() < deadline and run["status"] not in ("completed", "failed"):
        time.sleep(0.05)
        run = client.get(f"/api/projects/{hello_project}/tests/{leaf_run['id']}").json()
    assert run["status"] == "completed", run
    return session_id, leaf_run["id"]


def test_all_signals_aggregation_builds_each_runs_observations_only_once(monkeypatch, client, hello_project):
    session_id, run_id = _make_completed_run(client, hello_project)

    calls = []
    original = _AggregationJob._observations_for_run

    def counting(self, run_id):
        calls.append(run_id)
        return original(self, run_id)

    monkeypatch.setattr(_AggregationJob, "_observations_for_run", counting)

    test_service = client.app.state.test_service
    job = AllSignalsAggregationJob(
        test_service, hello_project, "turn_by_turn", [session_id], ["foo", "bar", "baz"],
    )
    test_service._submit(job)

    assert _wait_until(lambda: job.is_done())
    assert calls == [run_id]


def test_all_signals_aggregation_coalesces_concurrent_observation_building(monkeypatch, client, hello_project):
    """With several worker threads (see JobQueue's max_concurrent) all four
    signal jobs' first step can genuinely run at once — a plain shared
    dict alone doesn't stop them from each seeing a miss and rebuilding in
    parallel before any of them finishes writing. Slows the real build
    down to widen that race window, and asserts it still only ever runs
    once — see SignalAggregationJob.SharedObservationsCache."""
    session_id, run_id = _make_completed_run(client, hello_project)

    calls = []
    original = _AggregationJob._observations_for_run

    def slow_counting(self, run_id):
        calls.append(run_id)
        time.sleep(0.2)
        return original(self, run_id)

    monkeypatch.setattr(_AggregationJob, "_observations_for_run", slow_counting)

    test_service = client.app.state.test_service
    concurrent_queue = JobQueue(max_concurrent=4, broadcaster=test_service._job_queue._broadcaster)
    monkeypatch.setattr(test_service, "_job_queue", concurrent_queue)

    job = AllSignalsAggregationJob(
        test_service, hello_project, "turn_by_turn", [session_id], ["a", "b", "c", "d"],
    )
    test_service._submit(job)

    assert _wait_until(lambda: job.is_done(), timeout=5.0)
    assert calls == [run_id]
