from __future__ import annotations

import threading
import time

import pytest

from session import Session

pytestmark = pytest.mark.contract


def _wait_for_terminal_state_job(client, project_name, job_id, timeout=8.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/projects/{project_name}/state-jobs/{job_id}").json()
        if job is not None and job["status"] in ("completed", "failed"):
            return job
        time.sleep(interval)
    return client.get(f"/api/projects/{project_name}/state-jobs/{job_id}").json()


def _make_labeled_session_for(client, app_db, project_name, username):
    app_db.set_active_project_name(project_name, username)
    with Session().impersonate(username):
        session = client.get("/api/chat/session").json()
        turn = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}).json()
        client.put(
            f"/api/chat/messages/{turn['assistant_message_id']}/expected-state", json={"expected_state": "Hello"},
        )
        client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    return session["id"]


def test_root_play_fires_every_branch_concurrently_without_failing(client, app_db, hello_project):
    _make_labeled_session_for(client, app_db, hello_project, "alice")
    _make_labeled_session_for(client, app_db, hello_project, "bob")

    results = {}

    def launch(name, path):
        response = client.post(f"/api/projects/{hello_project}{path}", json={"strategy": "turn_by_turn"})
        results[name] = response

    threads = [
        threading.Thread(target=launch, args=("sessions", "/sessions/test")),
        threading.Thread(target=launch, args=("states", "/states/Hello/test")),
        threading.Thread(target=launch, args=("users", "/users/aggregation")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for name, response in results.items():
        assert response.status_code == 200, (name, response.text)
        job = _wait_for_terminal_state_job(client, hello_project, response.json()["job_id"])
        assert job["status"] == "completed", (name, job)
