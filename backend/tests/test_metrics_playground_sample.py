"""Exercises samples/Metrics Playground.zip — a project built specifically
to declare triggers over every core metric (see its index.yml), used here
as an end-to-end check that the sample stays loadable and its triggers
behave as documented.

Every assertion below cross-checks POST /api/triggers/preview's `result`
against the *actual* value GET /api/projects/{project_name}/metrics
reports for the same metric, rather than hand-deriving the
metrics_framework formulas here —
that's exactly the integration this sample exists to prove: that a
trigger expression sees the same metric values the Inspector's Metrics
tab does. Real chat turns aren't used (auto-tracking would call the AI to
evaluate the "mood" signal, which FakeAiService can't do meaningfully) —
sessions/state moves are driven directly through session-management and
manual-action endpoints instead, neither of which touches the AI.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"


def _upload_and_activate(client, name: str = "metrics-playground"):
    content = (SAMPLES_DIR / "Metrics Playground.zip").read_bytes()
    response = client.put(f"/api/projects/{name}", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text
    response = client.put(f"/api/projects/{name}/activate")
    assert response.status_code == 200, response.text
    response = client.post(f"/api/projects/{name}/publish", json={})
    assert response.status_code == 200, response.text
    return name


def _metric_values(client) -> dict[str, float]:
    return {m["name"]: m["value"] for m in client.get("/api/projects/metrics-playground/metrics").json()}


def _enter_engaged(client, session: dict):
    # "warm_up" also carries a trigger, but manual invocation (like a
    # user clicking its button) never checks it — see Automaton.move.
    response = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "warm_up"})
    assert response.status_code == 200, response.text
    assert response.json()["state"]["key"] == "engaged"


@pytest.mark.contract
def test_the_sample_loads_and_starts_at_lobby(client):
    _upload_and_activate(client)

    session = client.get("/api/chat/session").json()

    assert session["start_state"] == "lobby"


@pytest.mark.regression
def test_warm_up_fires_on_engagement_alone_right_after_bootstrap(client):
    _upload_and_activate(client)
    # A freshly bootstrapped session (one session, no messages yet) already
    # scores "engagement" a few points above zero from its own session
    # count alone — enough to clear "lobby"'s "engagement >= 4" trigger
    # with no messages sent at all.
    client.get("/api/chat/session")

    response = client.post("/api/triggers/preview", json={"signals": {}})

    assert response.status_code == 200
    body = {p["action_name"]: p for p in response.json()}
    assert body["warm_up"]["result"] is True
    assert _metric_values(client)["engagement"] >= 4


@pytest.mark.regression
def test_every_engaged_branch_matches_its_own_live_metric_or_signal_value(client):
    _upload_and_activate(client)
    session = client.get("/api/chat/session").json()
    _enter_engaged(client, session)
    values = _metric_values(client)

    response = client.post("/api/triggers/preview", json={"signals": {"mood": 100}})

    assert response.status_code == 200
    results = {p["action_name"]: p["result"] for p in response.json()}
    assert results["notice_mood"] == (100 >= 70)
    assert results["notice_combo"] == (100 >= 40 and values["engagement"] >= 10)
    assert results["notice_engagement"] == (values["engagement"] >= 20)
    assert results["notice_stability"] == (values["state_stability"] >= 80)
    assert results["notice_signal_stability"] == (values["signal_stability"] >= 1)


@pytest.mark.contract
def test_metric_values_never_include_a_non_session_scoped_metric(client):
    """retention/activity_consistency's own scope is
    {all_sessions_per_user, all_sessions} (see MetricCalculator.scope) —
    never one_session, the only context a chat turn's own trigger
    evaluation (and this sample's own Inspector-facing /api/chat/metrics)
    ever runs in (see AnalyticsCalculator's own default-metric
    filtering) — so neither ever appears here, and the sample's own
    index.yml deliberately has no trigger referencing either."""
    _upload_and_activate(client)
    client.get("/api/chat/session")

    values = _metric_values(client)

    assert "retention" not in values
    assert "activity_consistency" not in values


@pytest.mark.regression
def test_notice_combo_needs_both_the_signal_and_the_metric_side_true(client):
    _upload_and_activate(client)
    session = client.get("/api/chat/session").json()
    _enter_engaged(client, session)
    # Right after bootstrap, engagement's session-only baseline clears
    # "lobby"'s low threshold (>= 4) but not "notice_combo"'s higher one
    # (>= 10) — so a high mood value satisfies notice_mood but must not be
    # enough, by itself, to satisfy notice_combo's "and".
    assert _metric_values(client)["engagement"] < 10

    response = client.post("/api/triggers/preview", json={"signals": {"mood": 100}})

    results = {p["action_name"]: p["result"] for p in response.json()}
    assert results["notice_mood"] is True
    assert results["notice_combo"] is False


@pytest.mark.regression
def test_more_sessions_raise_engagement_without_any_ai_call(client):
    _upload_and_activate(client)
    session = client.get("/api/chat/session").json()
    _enter_engaged(client, session)

    # POST /api/chat/sessions never touches the AI/auto-tracker (see
    # ChatService.create_session) — a clean way to grow engagement's own
    # session-based component in a test.
    for _ in range(6):
        response = client.post("/api/chat/sessions")
        assert response.status_code == 200

    values = _metric_values(client)
    assert values["engagement"] >= 20

    response = client.post("/api/triggers/preview", json={"signals": {"mood": 100}})
    results = {p["action_name"]: p["result"] for p in response.json()}
    assert results["notice_engagement"] is True
    # Untouched by session count alone: needs real signal history (see
    # the sample's own file-level comment).
    assert results["notice_signal_stability"] is False
