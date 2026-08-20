"""Integration tests for the "Stati" branch's own aggregation job —
POST/GET /api/projects/{project_name}/states/{state_key}/test|state-jobs —
exercises BenchmarkRunService.start_job end to end: launching/reusing
session-scoped sub-runs, waiting on them via the persisted queue while
running itself on the ephemeral one, and aggregating Signal Accuracy.
"""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.contract


def _wait_for_terminal_state_job(client, project_name, job_id, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/projects/{project_name}/state-jobs/{job_id}").json()
        if job is not None and job["status"] in ("completed", "failed"):
            return job
        time.sleep(interval)
    return client.get(f"/api/projects/{project_name}/state-jobs/{job_id}").json()


def _make_session_annotated_at_hello(client):
    session = client.get("/api/chat/session").json()
    turn = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}).json()
    client.put(
        f"/api/chat/messages/{turn['assistant_message_id']}/expected-state", json={"expected_state": "Hello"},
    )
    return session["id"]


def test_state_test_aggregates_signal_accuracy_across_sessions(client, hello_project):
    _make_session_annotated_at_hello(client)

    response = client.post(
        f"/api/projects/{hello_project}/states/Hello/test", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]

    job = _wait_for_terminal_state_job(client, hello_project, job_id)

    assert job["status"] == "completed", job
    assert job["result"] is not None
    import json
    result = json.loads(job["result"])
    assert result["name"] == "signal_accuracy"


def test_state_test_with_no_touching_sessions_still_completes(client, hello_project):
    # No session anywhere has expected_state == "Hello" yet in this test's
    # own fresh app_db — an empty aggregation must still complete cleanly,
    # not hang or fail.
    response = client.post(
        f"/api/projects/{hello_project}/states/Hello/test", json={"strategy": "turn_by_turn"},
    )
    job_id = response.json()["job_id"]

    job = _wait_for_terminal_state_job(client, hello_project, job_id)

    assert job["status"] == "completed", job


def test_get_state_job_returns_none_for_unknown_id(client, hello_project):
    response = client.get(f"/api/projects/{hello_project}/state-jobs/999999")
    assert response.status_code == 200
    assert response.json() is None


def test_get_project_states_lists_real_state_keys(client, hello_project):
    response = client.get(f"/api/projects/{hello_project}/states")
    assert response.status_code == 200
    assert response.json() == ["Hello"]
