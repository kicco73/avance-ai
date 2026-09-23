from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from conftest import chat_socket, parse_sse_result, session_of

pytestmark = pytest.mark.contract


def _yml(on_exit: str) -> str:
    return (
        "project:\n  id: proj\n"
        "init-action:\n  target: a\n"
        "env:\n"
        "  mood:\n    type: string\n    ai-definition: the mood\n"
        "  slot:\n    type: list\n    ai-definition: the slots\n"
        "states:\n"
        "  a:\n"
        "    input-processor: ai\n"
        "    contextual-prompt: hi\n"
        "    actions:\n"
        "      - name: go\n"
        "        target: b\n"
        "        on-exit: |\n"
        + "".join(f"          {line}\n" for line in on_exit.splitlines())
        + "  b:\n"
        "    input-processor: ai\n"
        "    contextual-prompt: there\n"
        "    actions:\n"
        "      - name: back\n"
        "        target: a\n"
    )


def _published(client, on_exit: str) -> str:
    resp = client.post("/api/skills/platform/projects/upload", content=_yml(on_exit).encode(), headers={"Content-Type": "application/x-yaml"})
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


def _entered_and_pressed(client, project_id: str, action_name: str) -> tuple[int, list[dict]]:
    with chat_socket(client) as ws:
        ws.send_json({"type": "unsubscribe", "events": ["env.changed"]})
        ws.send_json({"type": "session.enter", "project_id": project_id, "session_type": "live"})
        session_id = session_of(_until_buttons(ws))
        ws.send_json({"type": "input.button", "session_id": session_id, "id": action_name})
        return session_id, _until_buttons(ws, after="state.changed")


def _bindings(frames: list[dict]) -> list[dict]:
    return [frame["values"] for frame in frames if frame["type"] == "env.bindings"]


def test_a_key_bound_and_then_written_by_the_same_script_reaches_the_chat_with_its_new_value(client):
    project_id = _published(client, "chat.bind_env(env.mood)\nenv.mood = 'happy'\nenv.slot = ['x']")

    _, frames = _entered_and_pressed(client, project_id, "go")

    assert _bindings(frames)[-1] == {"mood": "happy"}


def test_a_chat_entering_a_session_is_told_the_values_bound_there(client):
    project_id = _published(client, "env.mood = 'happy'\nchat.bind_env(env.mood)")
    _entered_and_pressed(client, project_id, "go")

    with chat_socket(client) as ws:
        ws.send_json({"type": "session.enter", "project_id": project_id, "session_type": "live"})
        frames = _until_buttons(ws)

    assert _bindings(frames) == [{"mood": "happy"}]


def test_unbinding_tells_the_chat_nothing_is_bound_any_more(client):
    project_id = _published(client, "env.mood = 'happy'\nchat.bind_env(env.mood)\nchat.unbind_env_all()")

    _, frames = _entered_and_pressed(client, project_id, "go")

    assert _bindings(frames)[-1] == {}


@pytest.mark.parametrize(("on_exit", "refusal"), [
    ("chat.bind_env(env.slot)", "'slot' is a list"),
    ("chat.bind_env(env.nope)", "env key 'nope' is not declared"),
    ("chat.bind_env('mood')", "chat.bind_env takes exactly one env.<key>"),
])
def test_bind_env_is_refused_at_build_time_unless_its_argument_is_a_declared_non_list_env_key(on_exit, refusal):
    with pytest.raises(ValueError, match=refusal):
        AutomatonBuilder().build({"index.yml": _yml(on_exit)})
