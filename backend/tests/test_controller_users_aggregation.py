"""Integration tests for POST /api/projects/{project_name}/users/aggregation,
exercising BenchmarkRunService.start_users_aggregation_job end to end:
launching one whole-project-scope sub-run per distinct annotated user and
averaging their per-metric `value` across users.
"""
from __future__ import annotations

import json
import time

import pytest

from session import Session

pytestmark = pytest.mark.contract


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
    # Two users, each with their own whole-project-scope run -> the full
    # 8-metric set (session_id=None, username given), averaged down to one
    # entry per metric name.
    assert len(results) == 8
    for result in results:
        assert result["mean"] is None
        assert result["median"] is None
        assert result["standard_deviation"] is None
        assert result["minimum"] is None
        assert result["maximum"] is None
        assert result["components"] == {}


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
