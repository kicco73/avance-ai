from __future__ import annotations

import threading
import time

import pytest

from system.web_session import WebSession

from conftest import chat_turn, enter_chat, session_of

pytestmark = pytest.mark.contract


def _wait_until(predicate, timeout=8.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _make_labeled_session_for(client, app_db, project_id, username):
    app_db.set_active_project_id(project_id, username)
    with WebSession().impersonate(username):
        session_id = session_of(enter_chat(client, project_id))
        turn = chat_turn(client, session_id, "hi")
        client.put(
            f"/api/skills/platform/messages/{turn['assistant_message_id']}/expected-state", json={"expected_state": "Hello"},
        )
        client.put(f"/api/skills/platform/sessions/{session_id}/labeled", json={"labeled": True})
    return session_id


def test_root_play_fires_every_branch_concurrently_without_failing(client, app_db, hello_project):
    _make_labeled_session_for(client, app_db, hello_project, "alice")
    _make_labeled_session_for(client, app_db, hello_project, "bob")

    results = {}

    # A real concurrent request arrives with its own valid Session context
    # (set by AuthMiddleware); a bare threading.Thread here would not
    # inherit the calling thread's contextvars at all, so each thread
    # establishes its own — same username this test is already running as.
    username = WebSession().user

    def launch(name, path):
        WebSession().user = username
        response = client.post(
            f"/api/skills/testing/projects/{hello_project}{path}", json={"strategy": "turn_by_turn"}
        )
        results[name] = response

    threads = [
        threading.Thread(target=launch, args=("sessions", "/runs/sessions")),
        threading.Thread(target=launch, args=("states", "/runs/states/Hello")),
        threading.Thread(target=launch, args=("users", "/aggregations/users")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for name, response in results.items():
        assert response.status_code == 200, (name, response.text)

    assert _wait_until(lambda: client.get(
        f"/api/skills/testing/projects/{hello_project}/aggregations/result",
        params={"kind": "sessions", "strategy": "turn_by_turn"},
    ).status_code == 200)
    assert _wait_until(lambda: client.get(
        f"/api/skills/testing/projects/{hello_project}/aggregations/result",
        params={"kind": "state", "target": "Hello", "strategy": "turn_by_turn"},
    ).status_code == 200)
    assert _wait_until(lambda: client.get(
        f"/api/skills/testing/projects/{hello_project}/aggregations/result",
        params={"kind": "users", "strategy": "turn_by_turn"},
    ).status_code == 200)


def test_root_aggregation_resolves_its_full_two_level_dependency_chain(client, app_db, hello_project):
    _make_labeled_session_for(client, app_db, hello_project, "alice")
    _make_labeled_session_for(client, app_db, hello_project, "bob")

    response = client.post(
        f"/api/skills/testing/projects/{hello_project}/aggregations/root", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text

    for kind in ("sessions", "all_states", "users", "all_signals"):
        assert _wait_until(lambda kind=kind: client.get(
            f"/api/skills/testing/projects/{hello_project}/aggregations/result",
            params={"kind": kind, "strategy": "turn_by_turn"},
        ).status_code == 200), kind
