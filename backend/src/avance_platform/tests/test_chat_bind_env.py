from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from conftest import chat_action_frames, enter_chat, parse_sse_result, session_of

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


def test_bind_env_reaches_the_browser_with_the_key_and_its_current_value(client):
    yml = _yml("env.mood = 'happy'\nchat.bind_env(env.mood)\nchat.unbind_env_all()")
    resp = client.post("/api/skills/platform/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    assert client.post(f"/api/core/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200
    session_id = session_of(enter_chat(client, project_id))

    frames = chat_action_frames(client, session_id, "go")

    assert [frame["task"] for frame in frames if frame["type"] == "ui.notification"] == [
        'bind_env("mood", "happy")\nunbind_env_all()',
    ]


@pytest.mark.parametrize(("on_exit", "refusal"), [
    ("chat.bind_env(env.slot)", "'slot' is a list"),
    ("chat.bind_env(env.nope)", "env key 'nope' is not declared"),
    ("chat.bind_env('mood')", "chat.bind_env takes exactly one env.<key>"),
])
def test_bind_env_is_refused_at_build_time_unless_its_argument_is_a_declared_non_list_env_key(on_exit, refusal):
    with pytest.raises(ValueError, match=refusal):
        AutomatonBuilder().build({"index.yml": _yml(on_exit)})
