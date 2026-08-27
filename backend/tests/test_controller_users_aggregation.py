"""Integration tests for POST /api/projects/{project_name}/users/aggregation,
exercising TestService.start_users_aggregation_job end to end:
pooling each distinct annotated user's own labeled-session sub-runs, then
averaging each user's own pooled result across users.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from session import Session
from testing.test_service import UsersAggregationJob

pytestmark = pytest.mark.contract


def _wait_for_terminal_status(client, project_name, run_id, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/projects/{project_name}/tests/{run_id}").json()
        if run["status"] in ("completed", "failed"):
            return run
        time.sleep(interval)
    return client.get(f"/api/projects/{project_name}/tests/{run_id}").json()


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

    result = _wait_for_aggregate_result(client, hello_project, "users", "turn_by_turn")
    assert result.status_code == 200, result.text
    results = result.json()
    assert len(results) == 8
    for entry in results:
        if entry["sample_count"]:
            assert entry["mean"] == entry["value"]
            assert entry["median"] is not None
            assert entry["standard_deviation"] is not None


def test_users_aggregation_with_a_single_user_matches_that_users_own_run(client, app_db, hello_project):
    _make_labeled_session_for(client, app_db, hello_project, "alice")

    own_response = client.post(
        f"/api/projects/{hello_project}/users/alice/test", json={"strategy": "turn_by_turn"},
    )
    assert own_response.status_code == 200, own_response.text
    own_result = _wait_for_aggregate_result(client, hello_project, "user_sessions", "turn_by_turn", target="alice")
    assert own_result.status_code == 200, own_result.text

    response = client.post(
        f"/api/projects/{hello_project}/users/aggregation", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text
    aggregated_result = _wait_for_aggregate_result(client, hello_project, "users", "turn_by_turn")
    assert aggregated_result.status_code == 200, aggregated_result.text

    assert aggregated_result.json() == own_result.json()


def test_sessions_run_reuses_an_existing_fresh_session_run_instead_of_replaying(client, app_db, hello_project):
    session_id = _make_labeled_session_for(client, app_db, hello_project, "alice")

    leaf_run = client.post(
        f"/api/projects/{hello_project}/tests",
        json={"session_id": session_id, "strategy": "turn_by_turn"},
    ).json()
    _wait_for_terminal_status(client, hello_project, leaf_run["id"])

    response = client.post(f"/api/projects/{hello_project}/sessions/test", json={"strategy": "turn_by_turn"})
    assert response.status_code == 200, response.text
    result = _wait_for_aggregate_result(client, hello_project, "sessions", "turn_by_turn")
    assert result.status_code == 200, result.text

    runs = client.get(f"/api/projects/{hello_project}/tests?session_id={session_id}").json()
    assert [run["id"] for run in runs] == [leaf_run["id"]]


def test_users_aggregation_with_no_annotated_users_still_completes(client, hello_project):
    response = client.post(
        f"/api/projects/{hello_project}/users/aggregation", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text

    result = _wait_for_aggregate_result(client, hello_project, "users", "turn_by_turn")
    assert result.status_code == 200, result.text
    assert result.json() == []


def test_users_aggregation_rerun_skips_dependency_resolution_when_cached(client, app_db, hello_project):
    """Once 'users' is cached (draft unchanged since), re-running it must
    resolve straight from that cache in _prepare() itself, before ever
    calling _resolve_or_construct_dependencies() — never reconstructing
    its underlying per-user, per-session TestReplayJob dependency tree
    again just to re-derive an answer it already has."""
    _make_labeled_session_for(client, app_db, hello_project, "alice")

    original = UsersAggregationJob._resolve_or_construct_dependencies
    calls = []

    def spy(self):
        calls.append(1)
        return original(self)

    with patch.object(UsersAggregationJob, "_resolve_or_construct_dependencies", spy):
        first = client.post(f"/api/projects/{hello_project}/users/aggregation", json={"strategy": "turn_by_turn"})
        assert first.status_code == 200, first.text
        cached = _wait_for_aggregate_result(client, hello_project, "users", "turn_by_turn")
        assert cached.status_code == 200, cached.text
        assert len(calls) == 1

        second = client.post(f"/api/projects/{hello_project}/users/aggregation", json={"strategy": "turn_by_turn"})
        assert second.status_code == 200, second.text
        recached = _wait_for_aggregate_result(client, hello_project, "users", "turn_by_turn")
        assert recached.status_code == 200, recached.text
        assert recached.json() == cached.json()

        # Unchanged: the second run's _prepare() resolved straight from
        # the cache and never touched dependency resolution at all.
        assert len(calls) == 1


def test_users_aggregation_rejects_unknown_strategy(client, hello_project):
    response = client.post(
        f"/api/projects/{hello_project}/users/aggregation", json={"strategy": "nonsense"},
    )
    assert response.status_code == 400
