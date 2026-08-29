"""Regression test for aborting an aggregation (e.g. "sessions") while it
still has live children (individual session TestReplayJobs still
running). abort() severs the parent-link from every child immediately,
before any child finishes -- so when each child later resolves and looks
for this job in its own .parents to notify it, it's already gone. Without
TestService._nudge_aborted forcing the orphaned job back into the queue,
its own terminal state (is_aborted=True) never gets broadcast at all,
since __forget() -- the only place that broadcasts it -- is never
reached; the job stays permanently orphaned.
"""
from __future__ import annotations

import time

import pytest

from session import Session

pytestmark = pytest.mark.contract


def _make_labeled_session(client, app_db, project_name, username):
    Session().user = username
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


def test_aborting_an_aggregation_with_live_children_still_gets_forgotten_and_broadcast(
    client, app_db, hello_project, monkeypatch,
):
    _make_labeled_session(client, app_db, hello_project, "alice")
    _make_labeled_session(client, app_db, hello_project, "bob")

    pushed = []
    test_service = client.app.state.test_service
    monkeypatch.setattr(
        test_service._job_queue._broadcaster, "push",
        lambda username, message: pushed.append(message),
    )

    response = client.post(f"/api/projects/{hello_project}/sessions/test", json={"strategy": "batch"})
    assert response.status_code == 200, response.text

    key = "batch:sessions-branch"
    assert _wait_until(lambda: key in test_service._jobs_by_key)

    delete_response = client.delete(f"/api/projects/{hello_project}/tests/jobs/{key}")
    assert delete_response.status_code == 200, delete_response.text

    def sessions_branch_broadcast_exited():
        return any(
            message["key"] == key and message["queue_status"] == "exited"
            for message in pushed
        )

    assert _wait_until(sessions_branch_broadcast_exited), pushed
    assert test_service._jobs_by_key[key].is_aborted()
