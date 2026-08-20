"""Integration tests for PUT .../comment — a domain expert's own free-
text note on a chat message (see Tracking.comment/TrackingService.
set_message_comment). Deliberately contrasted throughout against PUT
.../expected-state (see test_controller_benchmark.py): unlike that one,
a comment has no evaluation-point gating at all, so the same non-
evaluation-point message that 409s there must succeed here.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


@pytest.mark.contract
def test_put_comment_sets_and_is_visible_in_session_signals(client, hello_project):
    session = client.get("/api/chat/session").json()
    turn = client.post("/api/chat/messages", json={"message": "hi", "session_id": session["id"]}).json()
    message_id = turn["assistant_message_id"]

    response = client.put(f"/api/chat/messages/{message_id}/comment", json={"comment": "Worth a second look."})

    assert response.status_code == 200
    assert response.json()["comment"] == "Worth a second look."
    signals = client.get(f"/api/chat/sessions/{session['id']}/signals").json()
    row = next(r for r in signals if r["message_id"] == message_id)
    assert row["comment"] == "Worth a second look."


@pytest.mark.contract
def test_put_comment_clears_with_null(client, hello_project):
    session = client.get("/api/chat/session").json()
    turn = client.post("/api/chat/messages", json={"message": "hi", "session_id": session["id"]}).json()
    message_id = turn["assistant_message_id"]
    client.put(f"/api/chat/messages/{message_id}/comment", json={"comment": "note"})

    response = client.put(f"/api/chat/messages/{message_id}/comment", json={"comment": None})

    assert response.status_code == 200
    assert response.json()["comment"] is None


@pytest.mark.contract
def test_put_comment_strips_whitespace_and_treats_blank_as_clear(client, hello_project):
    session = client.get("/api/chat/session").json()
    turn = client.post("/api/chat/messages", json={"message": "hi", "session_id": session["id"]}).json()
    message_id = turn["assistant_message_id"]

    padded = client.put(f"/api/chat/messages/{message_id}/comment", json={"comment": "  spaced out  "})
    assert padded.json()["comment"] == "spaced out"

    blank = client.put(f"/api/chat/messages/{message_id}/comment", json={"comment": "   "})
    assert blank.json()["comment"] is None


@pytest.mark.contract
def test_put_comment_succeeds_for_a_non_evaluation_point_message(client, hello_project):
    """The exact scenario test_put_expected_state_is_409_for_a_non_
    evaluation_point_message (see test_controller_benchmark.py) 409s on —
    a comment must never be gated on this at all (see TrackingService.
    _require_commentable_message, which materializes a bare row rather
    than raising)."""
    session = client.get("/api/chat/session").json()
    turn = client.post("/api/chat/messages", json={"message": "hi", "session_id": session["id"]}).json()
    message_id = turn["assistant_message_id"]

    response = client.put(f"/api/chat/messages/{message_id}/comment", json={"comment": "still commentable"})

    assert response.status_code == 200
    assert response.json()["comment"] == "still commentable"


@pytest.mark.contract
def test_put_comment_is_404_for_an_unknown_message(client, hello_project):
    response = client.put("/api/chat/messages/999999/comment", json={"comment": "note"})
    assert response.status_code == 404


@pytest.mark.regression
def test_put_comment_does_not_disturb_expected_state_on_the_same_row(client, hello_project):
    session = client.get("/api/chat/session").json()
    client.post("/api/chat/messages", json={"message": "hi", "session_id": session["id"]})
    # "Hello world" evaluates on the user's own message by default — pick
    # whichever side already has a real Tracking row so this exercises a
    # comment written alongside an existing expected_state, not a bare
    # materialized row.
    session_id = session["id"]
    messages = client.get(f"/api/chat/messages?session_id={session_id}").json()
    user_message_id = next(m["id"] for m in messages if m["role"] == "user")
    client.put(f"/api/chat/messages/{user_message_id}/expected-state", json={"expected_state": "Hello"})

    response = client.put(f"/api/chat/messages/{user_message_id}/comment", json={"comment": "context for the reviewer"})

    assert response.status_code == 200
    body = response.json()
    assert body["comment"] == "context for the reviewer"
    assert body["expected_state"] == "Hello"
