from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from db.models import AiUsage

pytestmark = pytest.mark.contract

LABEL = "fake/fake-model"
SERIES = "/api/skills/platform/costs/series"


def _spend(
    project_id, *, username, session_id=None, kind="session", session_type="live", input_tokens=1_000_000, days_ago=0,
    turn_id=None,
):
    AiUsage.create(
        provider_label=LABEL, timestamp=datetime.utcnow() - timedelta(days=days_ago),
        input_tokens=input_tokens, output_tokens=0, kind=kind, session_type=session_type,
        project_id=project_id, username=username, session_id=session_id, turn_id=turn_id,
    )


def _last_24_hours(client, **params) -> float:
    return client.get(SERIES, params=params).json()["summary"]["last_24_hours"]


def test_the_users_branch_lists_those_who_spent_in_live_sessions_and_their_sessions(client, app_db, hello_project):
    session_id = app_db.create_chat_session("alice", hello_project, 1, title="First visit")
    _spend(hello_project, username="alice", session_id=session_id)
    _spend(hello_project, username="alice", session_id=999_999)
    _spend(hello_project, username="carol", session_id=8, session_type="preview")
    _spend(hello_project, username="bob", session_id=5, kind="benchmark", session_type=None)

    apps = client.get("/api/skills/platform/costs/apps").json()["apps"]
    users = client.get(f"/api/skills/platform/costs/apps/{hello_project}/users").json()["users"]
    sessions = client.get(f"/api/skills/platform/costs/apps/{hello_project}/users/alice/sessions").json()["sessions"]

    assert hello_project in [app["id"] for app in apps]
    assert [user["id"] for user in users] == ["alice"]
    assert sessions == [
        {"id": 999_999, "label": "Session 999999 (deleted)"},
        {"id": session_id, "label": "First visit"},
    ]


def test_each_branch_of_an_app_counts_only_its_own_spending_and_the_app_counts_all_of_it(client, hello_project):
    _spend(hello_project, username="alice", session_id=1)
    _spend(hello_project, username="bob", session_id=2)
    _spend(hello_project, username="alice", session_id=3, session_type="preview", input_tokens=2_000_000)
    _spend(hello_project, username="alice", session_id=4, session_type="test", input_tokens=3_000_000)
    _spend(hello_project, username="alice", session_id=1, kind="benchmark", session_type=None, input_tokens=4_000_000)

    assert _last_24_hours(client, project_id=hello_project) == 11.0
    assert _last_24_hours(client, project_id=hello_project, scope="test") == 4.0
    assert _last_24_hours(client, project_id=hello_project, scope="preview") == 2.0
    assert _last_24_hours(client, project_id=hello_project, scope="run") == 3.0
    assert _last_24_hours(client, project_id=hello_project, scope="users") == 2.0
    assert _last_24_hours(client, project_id=hello_project, scope="users", username="alice") == 1.0


def test_a_session_s_series_and_its_summary_cover_only_that_session(client, hello_project):
    _spend(hello_project, username="alice", session_id=1, days_ago=9)
    _spend(hello_project, username="alice", session_id=1, days_ago=3)
    _spend(hello_project, username="alice", session_id=1, input_tokens=500_000)
    _spend(hello_project, username="alice", session_id=2)

    body = client.get(SERIES, params={"project_id": hello_project, "scope": "users", "username": "alice", "session_id": 1}).json()

    assert body["currency"] == "EUR"
    assert [point["values"][LABEL] for point in body["history"]] == [1.0, 1.0, 0.5]
    assert body["summary"] == {
        "last_24_hours": 0.5, "last_7_days": 1.5, "mean_per_day": 2.5 / 10, "mean_per_week": 2.5 / 10 * 7,
    }


def test_an_unknown_scope_is_refused(client, hello_project):
    assert client.get(SERIES, params={"project_id": hello_project, "scope": "nope"}).status_code == 400


def _turns(client, project_id, scope) -> dict:
    return client.get("/api/skills/platform/costs/turns", params={"project_id": project_id, "scope": scope}).json()


def test_the_users_distribution_counts_every_call_of_a_turn_as_one_turn(client, hello_project):
    _spend(hello_project, username="alice", session_id=1, turn_id="t1", input_tokens=1_000_000)
    _spend(hello_project, username="alice", session_id=1, turn_id="t1", input_tokens=1_000_000)
    _spend(hello_project, username="bob", session_id=2, turn_id="t2", input_tokens=4_000_000)
    _spend(hello_project, username="alice", session_id=3, session_type="preview", turn_id="t3", input_tokens=9_000_000)

    body = _turns(client, hello_project, "users")

    assert (body["turns"], body["extra_per_turn"], body["mean"], body["median"]) == (2, 0.0, 3.0, 3.0)
    assert sum(bin_["count"] for bin_ in body["bins"]) == 2
    assert (body["bins"][0]["from"], body["bins"][-1]["to"]) == (2.0, 4.0)


def test_the_app_distribution_spreads_every_other_cost_evenly_over_the_live_turns(client, hello_project):
    _spend(hello_project, username="alice", session_id=1, turn_id="t1", input_tokens=1_000_000)
    _spend(hello_project, username="bob", session_id=2, turn_id="t2", input_tokens=3_000_000)
    _spend(hello_project, username="alice", session_id=3, session_type="preview", input_tokens=2_000_000)
    _spend(hello_project, username="alice", session_id=1, kind="benchmark", session_type=None, input_tokens=4_000_000)

    body = _turns(client, hello_project, "app")

    assert (body["turns"], body["extra_per_turn"], body["mean"]) == (2, 3.0, 5.0)
    assert (body["bins"][0]["from"], body["bins"][-1]["to"]) == (4.0, 6.0)


def test_a_distribution_for_a_scope_that_has_none_is_refused(client, hello_project):
    assert client.get("/api/skills/platform/costs/turns", params={"project_id": hello_project, "scope": "test"}).status_code == 400


def test_the_last_24_hours_are_a_rolling_window_not_the_calendar_day(client, hello_project):
    AiUsage.create(
        provider_label=LABEL, timestamp=datetime.utcnow() - timedelta(hours=20), input_tokens=1_000_000, output_tokens=0,
        kind="session", session_type="live", project_id=hello_project, username="alice", session_id=1,
    )
    AiUsage.create(
        provider_label=LABEL, timestamp=datetime.utcnow() - timedelta(hours=25), input_tokens=2_000_000, output_tokens=0,
        kind="session", session_type="live", project_id=hello_project, username="alice", session_id=1,
    )

    summary = client.get(SERIES, params={"project_id": hello_project}).json()["summary"]

    assert summary["last_24_hours"] == 1.0
    assert summary["last_7_days"] == 3.0
