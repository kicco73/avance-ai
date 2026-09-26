from __future__ import annotations

import io
import json
import zipfile

import pytest

from conftest import chat_action, chat_turn, enter_chat, installed_skill, parse_sse_result, session_of

pytestmark = pytest.mark.contract

_INDEX_YML = """
project:
  id: action_entries
init-action:
  target: welcome
  on-exit: env.level = ['easy', 'hard']
env:
  level:
    type: list
    ai-definition: the levels
states:
  welcome:
    input-processor: ai
    contextual-prompt: Welcome.
    actions:
      - name: start
        ui-button: Start
        target: opening
      - name: pick
        target: opening
        trigger: "choice.level == 'hard'"
  opening:
    input-processor: ai
    contextual-prompt: Talk.
    actions:
      - name: evaluate
        ui-button: Evaluate
        target: opening
"""


@pytest.fixture
def project_id(client) -> str:
    installed_skill("avance_platform")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.yml", _INDEX_YML)
    response = client.post(
        "/api/skills/platform/projects/upload", content=buffer.getvalue(), headers={"Content-Type": "application/zip"},
    )
    project_id = parse_sse_result(response)["project_id"]
    assert client.post(f"/api/core/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200
    return project_id


def _export(client, project_id) -> list[dict]:
    response = client.get(f"/api/skills/platform/projects/{project_id}/sessions/export")
    assert response.status_code == 200, response.text
    return response.json()


def _import(client, project_id, sessions: list[dict]) -> dict:
    response = client.post(
        f"/api/skills/platform/projects/{project_id}/sessions/import",
        files=[("files", ("sessions.json", json.dumps(sessions), "application/json"))],
    )
    assert response.status_code == 200, response.text
    return parse_sse_result(response)


def _shape(messages: list[dict]) -> list[tuple]:
    return [
        (entry["role"], entry.get("action"), json.dumps(entry.get("choice")))
        if entry["role"] == "action" else (entry["role"], entry["text"])
        for entry in messages
    ]


def _live_session(client, project_id, press: str) -> None:
    session_id = session_of(enter_chat(client, project_id))
    chat_turn(client, session_id, "before")
    chat_action(client, session_id, press)
    chat_turn(client, session_id, "after")


def test_a_manual_action_is_exported_as_an_action_entry_between_the_messages_around_it(client, project_id):
    _live_session(client, project_id, "start")

    [session] = _export(client, project_id)

    shape = _shape(session["messages"])
    action = shape.index(("action", "start", "null"))
    assert ("user", "before") in shape[:action]
    assert ("user", "after") in shape[action:]
    entry = session["messages"][action]
    assert (entry["old_state"], entry["new_state"], entry["origin"]) == ("welcome", "opening", "manual")


def test_a_choice_is_exported_as_an_action_entry_naming_its_key_and_option(client, project_id):
    _live_session(client, project_id, "choice:level:1")

    [session] = _export(client, project_id)

    entry = next(entry for entry in session["messages"] if entry["role"] == "action")
    assert entry["choice"] == {"key": "level", "option": "hard"}
    assert "action" not in entry
    shape = _shape(session["messages"])
    action = shape.index(("action", None, json.dumps({"key": "level", "option": "hard"})))
    assert ("user", "before") in shape[:action] and ("user", "after") in shape[action:]


def test_an_export_with_action_entries_round_trips_through_import(client, project_id):
    _live_session(client, project_id, "start")
    [live] = _export(client, project_id)
    live.update(type="imported", timestamp=None, datetime_end=None)
    live["messages"].append({"role": "action", "choice": {"key": "level", "option": "hard"}, "expected_state": "opening"})

    session_id = _import(client, project_id, [live])["last_session_id"]

    [again] = [session for session in _export(client, project_id) if session["type"] == "imported"]
    assert _shape(again["messages"]) == _shape(live["messages"])
    assert again["messages"][-1]["expected_state"] == "opening"
    assert session_id is not None


@pytest.mark.parametrize("entry", [
    {"role": "action"},
    {"role": "action", "action": "start", "choice": {"key": "level", "option": "hard"}},
    {"role": "action", "choice": "hard"},
])
def test_a_malformed_action_entry_fails_its_own_session_only(client, project_id, entry):
    good = {"name": "good", "type": "imported", "messages": [{"role": "user", "text": "hi"}]}
    bad = {"name": "bad", "type": "imported", "messages": [{"role": "user", "text": "hi"}, entry]}

    _import(client, project_id, [bad, good])

    assert [session["name"] for session in _export(client, project_id)] == ["good"]
