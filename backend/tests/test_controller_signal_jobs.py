"""Integration tests for POST /api/projects/{project_name}/signals/{signal_name}/test,
exercising BenchmarkRunService.start_signal_job end to end: pooling every
labeled session project-wide (not scoped by state) and aggregating one
signal's own accuracy across however many messages annotated it.
"""
from __future__ import annotations

import json
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


def _make_labeled_session(client):
    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})
    client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    return session["id"]


def test_signal_test_completes_with_no_samples_when_never_annotated(client, hello_project):
    _make_labeled_session(client)

    response = client.post(
        f"/api/projects/{hello_project}/signals/foo/test", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text
    job = _wait_for_terminal_state_job(client, hello_project, response.json()["job_id"])

    assert job["status"] == "completed", job
    result = json.loads(job["result"])
    assert result["name"] == "foo"
    assert result["sample_count"] == 0
    assert result["mean"] is None


def test_signal_test_reuses_an_existing_fresh_session_run_instead_of_replaying(client, hello_project):
    session_id = _make_labeled_session(client)

    leaf_run = client.post(
        f"/api/projects/{hello_project}/benchmark-runs",
        json={"session_id": session_id, "strategy": "turn_by_turn"},
    ).json()

    def _wait_run():
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            run = client.get(f"/api/projects/{hello_project}/benchmark-runs/{leaf_run['id']}").json()
            if run["status"] in ("completed", "failed"):
                return run
            time.sleep(0.05)
        return client.get(f"/api/projects/{hello_project}/benchmark-runs/{leaf_run['id']}").json()

    _wait_run()

    response = client.post(
        f"/api/projects/{hello_project}/signals/foo/test", json={"strategy": "turn_by_turn"},
    )
    job = _wait_for_terminal_state_job(client, hello_project, response.json()["job_id"])
    assert job["status"] == "completed", job

    runs = client.get(f"/api/projects/{hello_project}/benchmark-runs?session_id={session_id}").json()
    assert [run["id"] for run in runs] == [leaf_run["id"]]


def test_signal_test_rejects_unknown_strategy(client, hello_project):
    response = client.post(
        f"/api/projects/{hello_project}/signals/foo/test", json={"strategy": "nonsense"},
    )
    assert response.status_code == 400
