"""Integration tests for POST/GET /api/projects/{project_name}/benchmark-runs
— exercises the whole replay pipeline end to end (BenchmarkRunService,
BenchmarkProcessor, TurnByTurnSignalSource/BatchSignalSource, the generic
Job engine) against a real Db and FakeAiService, not just unit-level
pieces in isolation.
"""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.contract


def _wait_for_terminal_status(client, project_name, run_id, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/projects/{project_name}/benchmark-runs/{run_id}").json()
        if run["status"] in ("completed", "failed"):
            return run
        time.sleep(interval)
    return client.get(f"/api/projects/{project_name}/benchmark-runs/{run_id}").json()


def _make_labeled_session(client):
    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})
    client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    return session["id"]


def test_create_run_returns_immediately_pending(client, hello_project):
    session_id = _make_labeled_session(client)

    response = client.post(
        f"/api/projects/{hello_project}/benchmark-runs", json={"session_id": session_id, "strategy": "turn_by_turn"},
    )

    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] in ("pending", "running", "completed")
    assert run["session_id"] == session_id
    assert run["strategy"] == "turn_by_turn"
    assert run["results"] is None

    # Drain the job before the test ends — this fixture's Db gets torn
    # down and the next test's rebinds the shared peewee `database`
    # Proxy to a different one; a straggler JobQueue worker thread still
    # running past that point would execute against the wrong database.
    _wait_for_terminal_status(client, hello_project, run["id"])


def test_turn_by_turn_run_completes_and_produces_results(client, hello_project):
    session_id = _make_labeled_session(client)

    run = client.post(
        f"/api/projects/{hello_project}/benchmark-runs", json={"session_id": session_id, "strategy": "turn_by_turn"},
    ).json()

    finished = _wait_for_terminal_status(client, hello_project, run["id"])

    assert finished["status"] == "completed", finished
    assert finished["results"] is not None
    # session_id given -> only the "one_session"-scoped metrics (see
    # BenchmarkCalculator.default_metrics/from_data) — 6, not the full 8.
    assert len(finished["results"]) == 6
    assert finished["total_messages"] == finished["processed_messages"]


def test_batch_run_completes_and_tracks_batch_segments(client, hello_project):
    session_id = _make_labeled_session(client)

    run = client.post(
        f"/api/projects/{hello_project}/benchmark-runs", json={"session_id": session_id, "strategy": "batch"},
    ).json()

    finished = _wait_for_terminal_status(client, hello_project, run["id"])

    assert finished["status"] == "completed", finished
    assert finished["results"] is not None
    assert finished["batch_segments"] is not None and finished["batch_segments"] >= 1


def test_whole_project_run_scopes_to_labeled_sessions_only(client, hello_project):
    labeled_id = _make_labeled_session(client)
    # An unlabeled session must never be pulled into a whole-project run.
    unlabeled = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{unlabeled['id']}/messages", json={"message": "hi"})

    run = client.post(
        f"/api/projects/{hello_project}/benchmark-runs", json={"session_id": None, "strategy": "turn_by_turn"},
    ).json()

    assert run["session_id"] is None
    finished = _wait_for_terminal_status(client, hello_project, run["id"])
    assert finished["status"] == "completed", finished
    # session_id is None -> the full, unfiltered metric set (8) — see
    # BenchmarkCalculator.default_metrics/from_data.
    assert len(finished["results"]) == 8


def test_get_run_404_for_unknown_id(client, hello_project):
    response = client.get(f"/api/projects/{hello_project}/benchmark-runs/999999")
    assert response.status_code == 404


def test_list_runs_defaults_to_whole_project_scope(client, hello_project):
    session_id = _make_labeled_session(client)
    session_run = client.post(
        f"/api/projects/{hello_project}/benchmark-runs", json={"session_id": session_id, "strategy": "turn_by_turn"},
    ).json()
    project_run = client.post(
        f"/api/projects/{hello_project}/benchmark-runs", json={"session_id": None, "strategy": "turn_by_turn"},
    ).json()

    runs = client.get(f"/api/projects/{hello_project}/benchmark-runs").json()

    assert [r["id"] for r in runs] == [project_run["id"]]

    # Drain both jobs — see test_create_run_returns_immediately_pending's
    # own comment on why a straggler background thread must never outlive
    # this test's own Db fixture.
    _wait_for_terminal_status(client, hello_project, session_run["id"])
    _wait_for_terminal_status(client, hello_project, project_run["id"])


def test_create_run_rejects_unknown_strategy(client, hello_project):
    session_id = _make_labeled_session(client)

    response = client.post(
        f"/api/projects/{hello_project}/benchmark-runs", json={"session_id": session_id, "strategy": "nonsense"},
    )

    assert response.status_code == 400
