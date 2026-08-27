"""End-to-end verification that a project's `reactions:` vocabulary is
read off index.yml and reaches every HTTP surface the frontend actually
consumes: GET /api/state, a real chat turn's own state payload, the
message list (for a persisted bot-message reaction), and the PUT endpoint
that sets a user's own reaction on a bot message.
"""
from __future__ import annotations

import pytest

from conftest import parse_chat_turn_sse

pytestmark = pytest.mark.contract

PROJECT_YAML = """
project:
  id: reactions_demo

init-action:
  target: a

reactions:
  supportive:
    ui-label: "🙏"
    ui-description: Silent acknowledgment.
    definition: Use when a verbal response would feel clinical.
  encouraging:
    ui-label: "💪"
    definition: A light affirmation of effort.

states:
  a:
    contextual-prompt: hi
    reactions-enabled: true
  b:
    contextual-prompt: there
    reactions-enabled: false
"""


@pytest.fixture
def reactions_project(client):
    response = client.put(
        "/api/projects/reactions-demo", content=PROJECT_YAML.encode("utf-8"),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    assert client.put("/api/projects/reactions-demo/activate").status_code == 200
    assert client.post("/api/projects/reactions-demo/publish", json={}).status_code == 200
    return "reactions-demo"


def test_get_state_carries_the_reactions_vocabulary(client, reactions_project):
    response = client.get("/api/state")
    assert response.status_code == 200
    body = response.json()

    assert body["reactions"] == [
        {"key": "supportive", "ui_label": "🙏"},
        {"key": "encouraging", "ui_label": "💪"},
    ]
    # The AI-facing fields (definition/ui_description) never reach the
    # frontend — see Automaton.get_reaction_option_payload's own reasoning.
    for reaction in body["reactions"]:
        assert set(reaction) == {"key", "ui_label"}


def test_chat_turn_response_state_carries_reactions_too(client, reactions_project):
    session = client.get("/api/chat/session").json()

    turn = parse_chat_turn_sse(client.post(
        f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}
    ))

    assert turn["state"]["reactions"] == [
        {"key": "supportive", "ui_label": "🙏"},
        {"key": "encouraging", "ui_label": "💪"},
    ]


def test_message_list_and_reaction_endpoint_round_trip(client, reactions_project):
    session = client.get("/api/chat/session").json()
    turn = parse_chat_turn_sse(client.post(
        f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}
    ))
    assistant_id = turn["assistant_message_id"]

    # Freshly generated — no reaction set yet, but the field must already
    # be present (null), not missing, so the frontend's `message.reaction`
    # read never silently falls back to undefined.
    rows = client.get(f"/api/chat/sessions/{session['id']}/messages").json()
    assistant_row = next(r for r in rows if r["id"] == assistant_id)
    assert assistant_row["reaction"] is None

    response = client.put(
        f"/api/chat/messages/{assistant_id}/reaction", json={"reaction": "supportive"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["reaction"] == "supportive"

    rows = client.get(f"/api/chat/sessions/{session['id']}/messages").json()
    assistant_row = next(r for r in rows if r["id"] == assistant_id)
    assert assistant_row["reaction"] == "supportive"

    # Clearing (reaction: null) removes it again.
    response = client.put(f"/api/chat/messages/{assistant_id}/reaction", json={"reaction": None})
    assert response.status_code == 200
    assert response.json()["reaction"] is None


def test_reaction_on_someone_elses_message_is_404(client, reactions_project):
    response = client.put("/api/chat/messages/999999/reaction", json={"reaction": "supportive"})
    assert response.status_code == 404


def test_bots_own_reaction_is_captured_and_persisted_on_the_users_message(client, fake_ai_service, reactions_project):
    """The full loop the earlier unit tests only exercised piecemeal:
    the AI actually emits a [reaction] tag (FakeAiService.is_provider_with_schema
    is False, so text-extraction applies), TrackingProcessor's own
    on_receiving_metadata_* must capture it into Metadata.reaction, and
    process() must persist it onto the *user's* message, not the bot's own."""
    async def generate_stream_with_reaction(system_prompt, history, on_retry=None):
        fake_ai_service.calls.append((system_prompt, history))
        yield "Hello there. [reaction]supportive[/reaction]"

    fake_ai_service.generate_stream = generate_stream_with_reaction

    session = client.get("/api/chat/session").json()
    turn = parse_chat_turn_sse(client.post(
        f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"}
    ))
    user_message_id = turn["user_message_id"]
    assert user_message_id is not None

    # Carried on the turn response itself, not just persisted — the
    # frontend applies this live (see chatStore.js's own submitMessage),
    # without waiting on a full messages refetch to notice the DB write.
    assert turn["user_message_reaction"] == "supportive"

    rows = client.get(f"/api/chat/sessions/{session['id']}/messages").json()
    user_row = next(r for r in rows if r["id"] == user_message_id)
    assistant_row = next(r for r in rows if r["id"] == turn["assistant_message_id"])

    assert user_row["reaction"] == "supportive"
    # Never on the assistant's own new message — that's a different axis
    # (the user's own reaction to a bot message, set via the PUT endpoint).
    assert assistant_row["reaction"] is None
    # The visible reply text must never leak the raw tag markup.
    assert "[reaction]" not in "".join(m["content"] for m in turn["reply"])
