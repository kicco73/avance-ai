"""Firing an action fires the action's own task (its snippets reach the
browser over the websocket as a background ActionTask, never in this
response) and, separately, its own on-exit script — including any
chat.* calls, pushed synchronously, in this same request, never
hibernated. Both are the action's own, not anything read off the
destination state — two actions landing on the same state can disagree
on either.
"""
from __future__ import annotations

import pytest

from conftest import chat_action, chat_action_frames, enter_chat, parse_sse_result, session_of

pytestmark = pytest.mark.contract

YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go-quiet\n"
    "        target: b\n"
    "      - name: go-loud\n"
    "        target: b\n"
    "        on-exit: chat.celebrate()\n"
    "  b:\n"
    "    contextual-prompt: there\n"
    "    actions:\n"
    "      - name: back\n"
    "        target: a\n"
)


def _upload_and_get_session(client) -> int:
    resp = client.post("/api/skills/platform/projects/upload", content=YML.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.post(f"/api/core/projects/{project_id}/activate")
    assert resp.status_code == 200, resp.text
    resp = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text
    return session_of(enter_chat(client, project_id))


def test_manual_action_pushes_its_on_exits_own_chat_snippets_synchronously(client, app_db):
    """The notification reaches the very socket the button was pressed on,
    and before the choices that end the exchange — on-exit's own chat.*
    is pushed inside the action itself, never through the job queue."""
    session_id = _upload_and_get_session(client)

    frames = chat_action_frames(client, session_id, "go-loud")

    assert [frame for frame in frames if frame["type"] == "state.changed"][-1]["state"]["key"] == "b"
    assert app_db.list_tasks() == []
    assert [frame for frame in frames if frame["type"] == "ui.notification"] == [
        {"type": "ui.notification", "task": "celebrate()"},
    ]


def test_manual_action_without_on_exit_reports_none_even_for_the_same_target_state(client, app_db):
    session_id = _upload_and_get_session(client)

    moved = chat_action(client, session_id, "go-quiet")

    assert moved["state"]["key"] == "b"
    assert app_db.list_tasks() == []


def test_state_payload_never_carries_on_exit_itself(client):
    """on-exit is per-action, never present on the state payload itself."""
    session_id = _upload_and_get_session(client)

    moved = chat_action(client, session_id, "go-loud")

    assert "on-exit" not in moved["state"]
    for action in moved["state"]["actions"]:
        assert "on-exit" in action


def test_get_state_has_no_task_since_nothing_just_fired(client):
    _upload_and_get_session(client)

    resp = client.get("/api/core/state")

    assert resp.status_code == 200
    assert "task" not in resp.json()
