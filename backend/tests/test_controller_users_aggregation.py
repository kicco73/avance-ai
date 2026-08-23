"""Integration tests for POST /api/projects/{project_name}/users/aggregation,
exercising BenchmarkRunService.start_users_aggregation_job end to end:
pooling each distinct annotated user's own labeled-session sub-runs, then
averaging each user's own pooled result across users.
"""
from __future__ import annotations

import json
import time

import pytest

from session import Session

pytestmark = pytest.mark.contract


def _wait_for_terminal_status(client, project_name, run_id, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/projects/{project_name}/benchmark-runs/{run_id}").json()
        if run["status"] in ("completed", "failed"):
            return run
        time.sleep(interval)
    return client.get(f"/api/projects/{project_name}/benchmark-runs/{run_id}").json()


def _wait_for_terminal_state_job(client, project_name, job_id, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/projects/{project_name}/state-jobs/{job_id}").json()
        if job is not None and job["status"] in ("completed", "failed"):
            return job
        time.sleep(interval)
    return client.get(f"/api/projects/{project_name}/state-jobs/{job_id}").json()


def _make_labeled_session_for(client, app_db, project_name, username):
    # set_active_project_name directly, not PUT .../activate: that
    # endpoint's idempotent check reads the user's *current* active
    # project first, which raises for a user who's never activated
    # anything yet — unrelated to what's under test here.
    app_db.set_active_project_name(project_name, username)
    with Session().impersonate(username):
        session = client.get("/api/chat/session").json()
        client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})
        client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    return session["id"]


def test_users_aggregation_averages_value_across_users(client, app_db, hello_project):
    _make_labeled_session_for(client, app_db, hello_project, "alice")
    _make_labeled_session_for(client, app_db, hello_project, "bob")

    response = client.post(
        f"/api/projects/{hello_project}/users/aggregation", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]

    job = _wait_for_terminal_state_job(client, hello_project, job_id)

    assert job["status"] == "completed", job
    assert job["result"] is not None
    results = json.loads(job["result"])
    assert len(results) == 8
    for result in results:
        if result["sample_count"]:
            assert result["mean"] == result["value"]
            assert result["median"] is not None
            assert result["standard_deviation"] is not None


def test_users_aggregation_with_a_single_user_matches_that_users_own_run(client, app_db, hello_project):
    _make_labeled_session_for(client, app_db, hello_project, "alice")

    own_job = client.post(
        f"/api/projects/{hello_project}/users/alice/test", json={"strategy": "turn_by_turn"},
    )
    own_finished = _wait_for_terminal_state_job(client, hello_project, own_job.json()["job_id"])
    assert own_finished["status"] == "completed", own_finished
    own_results = json.loads(own_finished["result"])

    response = client.post(
        f"/api/projects/{hello_project}/users/aggregation", json={"strategy": "turn_by_turn"},
    )
    job = _wait_for_terminal_state_job(client, hello_project, response.json()["job_id"])
    assert job["status"] == "completed", job
    aggregated = json.loads(job["result"])

    assert aggregated == own_results


def test_sessions_run_reuses_an_existing_fresh_session_run_instead_of_replaying(client, app_db, hello_project):
    session_id = _make_labeled_session_for(client, app_db, hello_project, "alice")

    leaf_run = client.post(
        f"/api/projects/{hello_project}/benchmark-runs",
        json={"session_id": session_id, "strategy": "turn_by_turn"},
    ).json()
    _wait_for_terminal_status(client, hello_project, leaf_run["id"])

    response = client.post(f"/api/projects/{hello_project}/sessions/test", json={"strategy": "turn_by_turn"})
    job = _wait_for_terminal_state_job(client, hello_project, response.json()["job_id"])
    assert job["status"] == "completed", job

    runs = client.get(f"/api/projects/{hello_project}/benchmark-runs?session_id={session_id}").json()
    assert [run["id"] for run in runs] == [leaf_run["id"]]


def test_users_aggregation_with_no_annotated_users_still_completes(client, hello_project):
    response = client.post(
        f"/api/projects/{hello_project}/users/aggregation", json={"strategy": "turn_by_turn"},
    )
    job_id = response.json()["job_id"]

    job = _wait_for_terminal_state_job(client, hello_project, job_id)

    assert job["status"] == "completed", job
    assert json.loads(job["result"]) == []


def test_users_aggregation_rejects_unknown_strategy(client, hello_project):
    response = client.post(
        f"/api/projects/{hello_project}/users/aggregation", json={"strategy": "nonsense"},
    )
    assert response.status_code == 400
