"""Integration tests for POST /api/projects/{project_name}/states/{state_key}/test,
exercising BenchmarkRunService.start_job end to end: launching/reusing
session-scoped sub-runs and aggregating Signal Accuracy.
"""
from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.contract


def _wait_for_aggregate_result(client, project_name, kind, strategy, target=None, timeout=5.0, interval=0.05):
    params = {"kind": kind, "strategy": strategy}
    if target is not None:
        params["target"] = target
    deadline = time.monotonic() + timeout
    response = client.get(f"/api/projects/{project_name}/aggregate-result", params=params)
    while time.monotonic() < deadline and response.status_code != 200:
        time.sleep(interval)
        response = client.get(f"/api/projects/{project_name}/aggregate-result", params=params)
    return response


def _make_session_annotated_at_hello(client, *, labeled=False):
    session = client.get("/api/chat/session").json()
    turn = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}).json()
    client.put(
        f"/api/chat/messages/{turn['assistant_message_id']}/expected-state", json={"expected_state": "Hello"},
    )
    if labeled:
        client.put(f"/api/chat/sessions/{session['id']}/labeled", json={"labeled": True})
    return session["id"]


def test_state_test_aggregates_signal_accuracy_across_sessions(client, hello_project):
    _make_session_annotated_at_hello(client, labeled=True)

    response = client.post(
        f"/api/projects/{hello_project}/states/Hello/test", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text

    result = _wait_for_aggregate_result(client, hello_project, "state", "turn_by_turn", target="Hello")
    assert result.status_code == 200, result.text
    assert result.json()["name"] == "signal_accuracy"


def test_state_test_ignores_an_unlabeled_sessions_leftover_annotation(client, hello_project):
    session_id = _make_session_annotated_at_hello(client)

    client.post(f"/api/projects/{hello_project}/states/Hello/test", json={"strategy": "turn_by_turn"})

    result = _wait_for_aggregate_result(client, hello_project, "state", "turn_by_turn", target="Hello")
    assert result.status_code == 200, result.text
    assert result.json()["sample_count"] == 0
    assert client.get(f"/api/projects/{hello_project}/benchmark-runs?session_id={session_id}").json() == []


def test_state_test_with_no_touching_sessions_still_completes(client, hello_project):
    # An empty aggregation must still complete cleanly, not hang or fail.
    response = client.post(
        f"/api/projects/{hello_project}/states/Hello/test", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text

    result = _wait_for_aggregate_result(client, hello_project, "state", "turn_by_turn", target="Hello")
    assert result.status_code == 200, result.text


def test_get_aggregate_result_404_for_an_unknown_key(client, hello_project):
    response = client.get(
        f"/api/projects/{hello_project}/aggregate-result",
        params={"kind": "state", "target": "NoSuchState", "strategy": "turn_by_turn"},
    )
    assert response.status_code == 404


def test_get_project_states_lists_real_state_keys(client, hello_project):
    response = client.get(f"/api/projects/{hello_project}/states")
    assert response.status_code == 200
    assert response.json() == ["Hello"]
