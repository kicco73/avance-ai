from __future__ import annotations

import threading
import time

import pytest

from session import Session

from conftest import parse_chat_turn_sse

pytestmark = pytest.mark.contract


def _wait_until(predicate, timeout=8.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _make_labeled_session_for(client, app_db, project_name, username):
    app_db.set_active_project_name(project_name, username)
    with Session().impersonate(username):
        session = client.get("/api/chat/session").json()
        turn = parse_chat_turn_sse(client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}))
        client.put(
            f"/api/chat/messages/{turn['assistant_message_id']}/expected-state", json={"expected_state": "Hello"},
        )
        client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    return session["id"]


def test_root_play_fires_every_branch_concurrently_without_failing(client, app_db, hello_project):
    _make_labeled_session_for(client, app_db, hello_project, "alice")
    _make_labeled_session_for(client, app_db, hello_project, "bob")

    results = {}

    # A real concurrent request arrives with its own valid Session context
    # (set by AuthMiddleware); a bare threading.Thread here would not
    # inherit the calling thread's contextvars at all, so each thread
    # establishes its own — same username this test is already running as.
    username = Session().user

    def launch(name, path):
        Session().user = username
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

    assert _wait_until(lambda: client.get(
        f"/api/projects/{hello_project}/aggregate-result",
        params={"kind": "sessions", "strategy": "turn_by_turn"},
    ).status_code == 200)
    assert _wait_until(lambda: client.get(
        f"/api/projects/{hello_project}/aggregate-result",
        params={"kind": "state", "target": "Hello", "strategy": "turn_by_turn"},
    ).status_code == 200)
    assert _wait_until(lambda: client.get(
        f"/api/projects/{hello_project}/aggregate-result",
        params={"kind": "users", "strategy": "turn_by_turn"},
    ).status_code == 200)


def test_root_aggregation_resolves_its_full_two_level_dependency_chain(client, app_db, hello_project):
    _make_labeled_session_for(client, app_db, hello_project, "alice")
    _make_labeled_session_for(client, app_db, hello_project, "bob")

    response = client.post(
        f"/api/projects/{hello_project}/root/aggregation", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text

    for kind in ("sessions", "all_states", "users", "all_signals"):
        assert _wait_until(lambda kind=kind: client.get(
            f"/api/projects/{hello_project}/aggregate-result",
            params={"kind": kind, "strategy": "turn_by_turn"},
        ).status_code == 200), kind
