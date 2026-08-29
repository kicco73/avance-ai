"""Regression test for the "root" aggregation click: several branches
(sessions, states, users, signals) can all depend on the very same
underlying session replay. Before the fix, a second branch reaching that
same session saw its already-existing (still running) row and treated it
as "nothing to wait for", so its own aggregate could report completed
before the shared session replay actually finished — see
TestService._resolve_or_construct_session_run and
JobQueue.submit's own handling of an already-in-flight dependency.
"""
from __future__ import annotations

import time

import pytest

from session import Session

pytestmark = pytest.mark.contract


def _make_labeled_session(client, app_db, project_name, username):
    Session().user = username
    # activate_project_idempotent needs an already-active project to
    # compare against — set_active_project_name directly is the same
    # effect for a brand new username with no chat history yet.
    app_db.set_active_project_name(project_name, username)
    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})
    client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    Session().user = "user"
    return session["id"]


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_root_click_waits_for_a_session_shared_across_branches(client, app_db, hello_project):
    alice_session_id = _make_labeled_session(client, app_db, hello_project, "alice")
    bob_session_id = _make_labeled_session(client, app_db, hello_project, "bob")

    response = client.post(f"/api/projects/{hello_project}/root/aggregation", json={"strategy": "batch"})
    assert response.status_code == 200, response.text

    def users_result_ready():
        result = client.get(
            f"/api/projects/{hello_project}/aggregate-result", params={"kind": "users", "strategy": "batch"},
        )
        return result.status_code == 200

    assert _wait_until(users_result_ready)

    for session_id in (alice_session_id, bob_session_id):
        runs = client.get(f"/api/projects/{hello_project}/tests", params={"session_id": session_id}).json()
        batch_runs = [run for run in runs if run["strategy"] == "batch"]
        assert batch_runs and all(run["status"] == "completed" for run in batch_runs)
