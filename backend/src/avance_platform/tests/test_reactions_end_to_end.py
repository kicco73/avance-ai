"""End-to-end verification that a project's `reactions:` vocabulary is
read off index.yml and reaches everything the frontend actually consumes:
GET /api/core/state, the state a conversation is entered in, the transcript
(for a persisted bot-message reaction), and `input.reaction`, which sets
a person's own reaction on a bot message.
"""
from __future__ import annotations

import pytest

from conftest import (
    _frame_deadline, chat_socket, chat_turn, enter_chat, parse_sse_result, session_of,
    turn_frame_seconds,
)

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


def _react(client, session_id: int, message_id: int, reaction: str | None) -> list[dict]:
    frames = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({
                "type": "input.reaction", "session_id": session_id,
                "assistant_message_id": message_id, "reaction": reaction,
            })
            ws.send_json({"type": "session.recall", "session_id": session_id})
            while True:
                frames.append(ws.receive_json())
                if frames[-1]["type"] == "session.messages":
                    return frames[-1]["messages"]


@pytest.fixture
def reactions_project(client):
    response = client.post(
        "/api/skills/platform/projects/upload", content=PROJECT_YAML.encode("utf-8"),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    assert client.post(f"/api/skills/platform/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200
    return project_id


def test_get_state_carries_the_reactions_vocabulary(client, reactions_project):
    response = client.get("/api/core/state")
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


def test_entering_a_conversation_carries_reactions_too(client, reactions_project):
    frames = enter_chat(client, reactions_project)

    info = next(frame for frame in frames if frame["type"] == "session.info")

    assert info["state"]["reactions"] == [
        {"key": "supportive", "ui_label": "🙏"},
        {"key": "encouraging", "ui_label": "💪"},
    ]


def test_transcript_and_input_reaction_round_trip(client, reactions_project):
    session_id = session_of(enter_chat(client, reactions_project))
    turn = chat_turn(client, session_id, "hi")
    assistant_id = turn["assistant_message_id"]

    # Freshly generated — no reaction set yet, but the field must already
    # be present (null), not missing, so the frontend's `message.reaction`
    # read never silently falls back to undefined.
    rows = client.get(f"/api/core/sessions/{session_id}/history").json()
    assistant_row = next(r for r in rows if r["id"] == assistant_id)
    assert assistant_row["reaction"] is None

    rows = _react(client, session_id, assistant_id, "supportive")
    assistant_row = next(r for r in rows if r["id"] == assistant_id)
    assert assistant_row["reaction"] == "supportive"

    # Clearing (reaction: null) removes it again.
    rows = _react(client, session_id, assistant_id, None)
    assert next(r for r in rows if r["id"] == assistant_id)["reaction"] is None


NO_REACTIONS_PROJECT_YAML = """
project:
  id: no_reactions_demo

init-action:
  target: a

states:
  a:
    contextual-prompt: hi
    reactions-enabled: true
"""


@pytest.fixture
def no_reactions_project(client):
    """Same shape as reactions_project, but with no `reactions:` section
    declared at all — 'a' still opts in with reactions-enabled: true,
    which AutomatonBuilder happily parses (no build-time error, see
    test_automaton_builder_reactions.py) but Automaton.reactions_enabled_for
    should make a no-op at runtime regardless."""
    response = client.post(
        "/api/skills/platform/projects/upload", content=NO_REACTIONS_PROJECT_YAML.encode("utf-8"),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    assert client.post(f"/api/skills/platform/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200
    return project_id


def test_a_states_reactions_enabled_has_no_effect_without_a_declared_reactions_section(
    client, fake_ai_service, no_reactions_project,
):
    """Even a model that would report a 'reaction' field anyway (ignoring
    that it was never offered one, since TurnProtocol's own reaction_tags
    is empty here, so 'reaction' never enters the schema sent to it) must
    not have it captured — "if activated, no effect". The fake below
    checks `schema` the same way a real schema-constrained provider
    structurally can't emit a field outside it."""
    async def generate_stream_with_metadata_and_reaction(system_prompt, history, on_metadata, schema, tool_set=None):
        fake_ai_service.calls.append((system_prompt, history))
        if 'reaction' in schema:
            on_metadata('reaction', 'supportive')
        yield "Hello there."

    fake_ai_service.generate_stream_with_metadata = generate_stream_with_metadata_and_reaction

    session_id = session_of(enter_chat(client, no_reactions_project))
    turn = chat_turn(client, session_id, "hi")

    assert turn["user_message_reaction"] is None
    rows = client.get(f"/api/core/sessions/{session_id}/history").json()
    user_row = [r for r in rows if r["role"] == "user"][-1]
    assert user_row["reaction"] is None


def test_bots_own_reaction_is_captured_and_persisted_on_the_users_message(client, fake_ai_service, reactions_project):
    """The full loop the earlier unit tests only exercised piecemeal: the
    AI actually reports a 'reaction' field, TrackingProcessor's own
    on_receiving_metadata_* must capture it into Metadata.reaction, and
    process() must persist it onto the *user's* message, not the bot's own."""
    async def generate_stream_with_metadata_and_reaction(system_prompt, history, on_metadata, schema, tool_set=None):
        fake_ai_service.calls.append((system_prompt, history))
        on_metadata('reaction', 'supportive')
        yield "Hello there."

    fake_ai_service.generate_stream_with_metadata = generate_stream_with_metadata_and_reaction

    session_id = session_of(enter_chat(client, reactions_project))
    turn = chat_turn(client, session_id, "hi")
    user_message_id = turn["user_message_id"]
    assert user_message_id is not None

    # Carried on the turn response itself, not just persisted — the
    # frontend applies this live (see chatStore.js's own submitMessage),
    # without waiting on a full messages refetch to notice the DB write.
    assert turn["user_message_reaction"] == "supportive"

    rows = client.get(f"/api/core/sessions/{session_id}/history").json()
    user_row = next(r for r in rows if r["id"] == user_message_id)
    assistant_row = next(r for r in rows if r["id"] == turn["assistant_message_id"])

    assert user_row["reaction"] == "supportive"
    # Never on the assistant's own new message — that's a different axis
    # (the user's own reaction to a bot message, set via the PUT endpoint).
    assert assistant_row["reaction"] is None
    # The visible reply text must never leak the raw tag markup.
    assert "[reaction]" not in "".join(m["content"] for m in turn["reply"])
