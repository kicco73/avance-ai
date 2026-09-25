from __future__ import annotations

import time

import pytest

from conftest import chat_turn, enter_chat, session_of

pytestmark = pytest.mark.contract


def _make_labeled_session(client, project_id):
    session_id = session_of(enter_chat(client, project_id))
    chat_turn(client, session_id, "hi")
    chat_turn(client, session_id, "again")
    client.put(f"/api/skills/platform/sessions/{session_id}/labeled", json={"labeled": True})
    return session_id


def _wait_for_sessions_aggregate(client, project_id, strategy, timeout=5.0):
    params = {"kind": "sessions", "strategy": strategy}
    url = f"/api/skills/testing/projects/{project_id}/aggregations/result"
    deadline = time.monotonic() + timeout
    response = client.get(url, params=params)
    while response.status_code != 200 and time.monotonic() < deadline:
        time.sleep(0.05)
        response = client.get(url, params=params)
    assert response.status_code == 200, response.text


def test_export_holds_metric_definitions_and_the_tree_with_every_completed_result(client, hello_project):
    session_id = _make_labeled_session(client, hello_project)
    client.post(f"/api/skills/testing/projects/{hello_project}/runs/sessions", json={"strategy": "turn_by_turn"})
    _wait_for_sessions_aggregate(client, hello_project, "turn_by_turn")

    response = client.get(f"/api/skills/testing/projects/{hello_project}/export", params={"strategy": "turn_by_turn"})

    assert response.status_code == 200, response.text
    export = response.json()
    assert export["metrics"]["state_accuracy"]["label"] == "State Accuracy"
    assert export["metrics"]["state_accuracy"]["description"]

    run = export["run"]
    assert run["project_id"] == hello_project
    assert run["strategy"] == "turn_by_turn"
    assert set(run) >= {"sessions", "states", "users", "signals"}

    branch = run["sessions"]["results"]
    assert set(branch) == set(export["metrics"])
    assert set(branch["state_accuracy"]) >= {"value", "median", "standard_deviation", "sample_count"}

    session = run["sessions"]["sessions"][str(session_id)]
    assert session["turns"] == 2
    assert session["start_state"] == "Hello"
    assert session["end_state"] == "Hello"
    assert session["stale"] is False
    assert set(session["results"]) <= set(export["metrics"])
    assert session["results"]

    user_messages = [message for message in session["messages"] if message["role"] == "user"]
    assert [message["text"] for message in user_messages] == ["hi", "again"]
    assert all(message["replay"] is not None for message in user_messages)
    assert all(set(message) == {"role", "text", "timestamp", "expected", "replay"} for message in session["messages"])

    assert run["states"]["results"] is None
    assert run["users"]["results"] is None


def test_export_rejects_an_unknown_strategy(client, hello_project):
    response = client.get(f"/api/skills/testing/projects/{hello_project}/export", params={"strategy": "nonsense"})

    assert response.status_code == 400


def test_the_export_format_is_documented(client):
    response = client.get("/api/core/docs/benchmark-export")

    assert response.status_code == 200, response.text
    assert "# Benchmark export" in response.json()["content"]
