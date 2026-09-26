from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from conftest import chat_socket, parse_sse_result, session_of

pytestmark = pytest.mark.contract


def _yml(mood: str = "    ui-binding: true\n", slot: str = "") -> str:
    return (
        "project:\n  id: proj\n"
        "init-action:\n  target: a\n"
        "env:\n"
        "  mood:\n    type: string\n    ai-definition: the mood\n" + mood
        + "  slot:\n    type: list\n    ai-definition: the slots\n" + slot
        + "  level:\n    type: number\n"
        "states:\n"
        "  a:\n"
        "    input-processor: ai\n"
        "    contextual-prompt: hi\n"
        "    actions:\n"
        "      - name: go\n"
        "        target: b\n"
        "        on-exit: |\n"
        "          env.mood = 'happy'\n"
        "          env.level = 3\n"
        "  b:\n"
        "    input-processor: ai\n"
        "    contextual-prompt: there\n"
        "    actions:\n"
        "      - name: back\n"
        "        target: a\n"
    )


def _published(client) -> str:
    resp = client.post("/api/skills/platform/projects/upload", content=_yml().encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    assert client.post(f"/api/core/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200
    return project_id


def _until_buttons(ws, after: str = "state.buttons") -> list[dict]:
    frames = []
    while not frames or frames[-1]["type"] != "state.buttons" or after not in [frame["type"] for frame in frames]:
        frames.append(ws.receive_json())
    return frames


def _entered(client, project_id: str) -> list[dict]:
    with chat_socket(client) as ws:
        ws.send_json({"type": "session.enter", "project_id": project_id, "session_type": "live"})
        return _until_buttons(ws)


def _bindings(frames: list[dict]) -> list[dict]:
    return [frame["values"] for frame in frames if frame["type"] == "env.bindings"]


def test_a_chat_entering_a_session_is_told_the_current_value_of_every_ui_binding_key_and_only_those(client):
    project_id = _published(client)
    with chat_socket(client) as ws:
        ws.send_json({"type": "session.enter", "project_id": project_id, "session_type": "live"})
        session_id = session_of(_until_buttons(ws))
        ws.send_json({"type": "input.button", "session_id": session_id, "id": "go"})
        _until_buttons(ws, after="state.changed")

    assert _bindings(_entered(client, project_id)) == [{"mood": "happy"}]


def test_a_ui_binding_key_the_chat_follows_is_updated_by_the_regular_env_changed(client):
    project_id = _published(client)
    with chat_socket(client) as ws:
        ws.send_json({"type": "subscribe", "events": ["env.changed"]})
        ws.send_json({"type": "session.enter", "project_id": project_id, "session_type": "live"})
        session_id = session_of(_until_buttons(ws))
        ws.send_json({"type": "input.button", "session_id": session_id, "id": "go"})
        frames = _until_buttons(ws, after="state.changed")

    assert {"type": "env.changed", "session_id": session_id, "key": "mood", "value": "happy"} in frames


def test_the_env_card_turns_ui_binding_on_and_off(client):
    project_id = _published(client)
    route = f"/api/skills/platform/projects/{project_id}/env-keys/mood/ui-binding"

    turned_off = client.put(route, json={"value": False})
    turned_on = client.put(route, json={"value": True})

    assert turned_off.json()["ui_binding"] is False
    assert turned_on.json()["ui_binding"] is True
    assert client.put(route, json={"value": "yes"}).status_code == 400


@pytest.mark.parametrize(("mood", "slot", "refusal"), [
    ("", "    ui-binding: true\n", "a list can't be ui-binding"),
    ("    ui-binding: 'yes'\n", "", "'ui-binding' must be true or false"),
])
def test_ui_binding_is_refused_at_build_time_on_a_list_or_a_non_bool(mood, slot, refusal):
    with pytest.raises(ValueError, match=refusal):
        AutomatonBuilder().build({"index.yml": _yml(mood, slot)})
