"""PUT .../comment — a free-text note on a chat message. Unlike PUT
.../expected-state, a comment has no evaluation-point gating, so the same
non-evaluation-point message that 409s there must succeed here.
"""
from __future__ import annotations

import pytest

from conftest import chat_turn

pytestmark = pytest.mark.contract


@pytest.mark.contract
def test_put_comment_sets_and_is_visible_in_session_signals(client, hello_project):
    session = client.get("/api/skills/webchat/sessions/current").json()
    turn = chat_turn(client, session['id'], "hi")
    message_id = turn["assistant_message_id"]

    response = client.put(f"/api/skills/platform/messages/{message_id}/comment", json={"comment": "Worth a second look."})

    assert response.status_code == 200
    assert response.json()["comment"] == "Worth a second look."
    signals = client.get(f"/api/core/sessions/{session['id']}/signals").json()
    row = next(r for r in signals if r["message_id"] == message_id)
    assert row["comment"] == "Worth a second look."


@pytest.mark.contract
def test_put_comment_clears_with_null(client, hello_project):
    session = client.get("/api/skills/webchat/sessions/current").json()
    turn = chat_turn(client, session['id'], "hi")
    message_id = turn["assistant_message_id"]
    client.put(f"/api/skills/platform/messages/{message_id}/comment", json={"comment": "note"})

    response = client.put(f"/api/skills/platform/messages/{message_id}/comment", json={"comment": None})

    assert response.status_code == 200
    assert response.json()["comment"] is None


@pytest.mark.contract
def test_put_comment_strips_whitespace_and_treats_blank_as_clear(client, hello_project):
    session = client.get("/api/skills/webchat/sessions/current").json()
    turn = chat_turn(client, session['id'], "hi")
    message_id = turn["assistant_message_id"]

    padded = client.put(f"/api/skills/platform/messages/{message_id}/comment", json={"comment": "  spaced out  "})
    assert padded.json()["comment"] == "spaced out"

    blank = client.put(f"/api/skills/platform/messages/{message_id}/comment", json={"comment": "   "})
    assert blank.json()["comment"] is None


@pytest.mark.contract
def test_put_comment_succeeds_for_a_non_evaluation_point_message(client, hello_project):
    """A comment is never gated on evaluation-point status, unlike
    expected-state (see test_controller_benchmark.py)."""
    session = client.get("/api/skills/webchat/sessions/current").json()
    turn = chat_turn(client, session['id'], "hi")
    message_id = turn["assistant_message_id"]

    response = client.put(f"/api/skills/platform/messages/{message_id}/comment", json={"comment": "still commentable"})

    assert response.status_code == 200
    assert response.json()["comment"] == "still commentable"


@pytest.mark.contract
def test_put_comment_is_404_for_an_unknown_message(client, hello_project):
    response = client.put("/api/skills/platform/messages/999999/comment", json={"comment": "note"})
    assert response.status_code == 404


@pytest.mark.regression
def test_put_comment_does_not_disturb_expected_state_on_the_same_row(client, hello_project):
    session = client.get("/api/skills/webchat/sessions/current").json()
    chat_turn(client, session['id'], "hi")
    session_id = session["id"]
    messages = client.get(f"/api/skills/webchat/sessions/{session_id}/messages").json()
    # The evaluation point is the *assistant* line: a turn records what the
    # automaton decided, and the user's own message is not a decision. This
    # test used to pick the user side, where set_message_expected_state
    # answers 409 — and since nothing asserted that, the annotation it
    # meant to protect was never written, and the comment landed on a bare
    # row whose expected_state was null all along. Asserting the setup is
    # what stops the same silence coming back.
    annotated_id = messages[0]["id"]
    prepared = client.put(
        f"/api/skills/platform/messages/{annotated_id}/expected-state", json={"expected_state": "Hello"},
    )
    assert prepared.status_code == 200, prepared.text

    response = client.put(f"/api/skills/platform/messages/{annotated_id}/comment", json={"comment": "context for the reviewer"})

    assert response.status_code == 200
    body = response.json()
    assert body["comment"] == "context for the reviewer"
    assert body["expected_state"] == "Hello"
