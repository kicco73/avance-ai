"""Integration tests for POST /api/projects/{project_name}/signals/{signal_name}/test,
exercising TestService.start_signal_job end to end: pooling every
labeled session project-wide (not scoped by state) and aggregating one
signal's own accuracy across however many messages annotated it.
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


def _wait_for_run_terminal(client, project_name, run_id, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/projects/{project_name}/tests/{run_id}").json()
        if run["status"] in ("completed", "failed"):
            return run
        time.sleep(interval)
    return client.get(f"/api/projects/{project_name}/tests/{run_id}").json()


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

    result = _wait_for_aggregate_result(client, hello_project, "signal", "turn_by_turn", target="foo")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["name"] == "foo"
    assert body["sample_count"] == 0
    assert body["mean"] is None


def test_signal_test_reuses_an_existing_fresh_session_run_instead_of_replaying(client, hello_project):
    session_id = _make_labeled_session(client)

    leaf_run = client.post(
        f"/api/projects/{hello_project}/tests",
        json={"session_id": session_id, "strategy": "turn_by_turn"},
    ).json()
    _wait_for_run_terminal(client, hello_project, leaf_run["id"])

    response = client.post(
        f"/api/projects/{hello_project}/signals/foo/test", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text
    result = _wait_for_aggregate_result(client, hello_project, "signal", "turn_by_turn", target="foo")
    assert result.status_code == 200, result.text

    runs = client.get(f"/api/projects/{hello_project}/tests?session_id={session_id}").json()
    assert [run["id"] for run in runs] == [leaf_run["id"]]


def test_signal_test_rejects_unknown_strategy(client, hello_project):
    response = client.post(
        f"/api/projects/{hello_project}/signals/foo/test", json={"strategy": "nonsense"},
    )
    assert response.status_code == 400
