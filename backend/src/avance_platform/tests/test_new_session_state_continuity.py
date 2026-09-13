"""A new live session must resume wherever this user's conversation
already is — never replay init-action, which would both jump the visible
state back to the start and re-fire whatever task the project defines
for it. A new test/draft session is the opposite: always a fresh run
through init-action, regardless of where a previous draft session left off.
"""
from __future__ import annotations

import pytest

from conftest import (
    _frame_deadline, chat_action, chat_socket, enter_chat, parse_sse_result, session_of,
    turn_frame_seconds,
)

pytestmark = pytest.mark.contract

YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n  task: task.send_mail(user.email, 'hi')\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        target: b\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)


def _frames_until_buttons(client, payload: dict) -> list[dict]:
    frames = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json(payload)
            while True:
                frames.append(ws.receive_json())
                if frames[-1]["type"] in ("state.buttons", "session.blocked", "output.error"):
                    return frames


def _info_of(frames: list[dict]) -> dict:
    return next(frame for frame in frames if frame["type"] == "session.info")


def _created(client, project_id: str) -> dict:
    return _info_of(_frames_until_buttons(
        client, {"type": "session.create", "project_id": project_id, "session_type": "live"},
    ))


def _reopened(client, session_id: int) -> dict:
    return _info_of(_frames_until_buttons(client, {"type": "session.enter", "session_id": session_id}))


def _upload_and_publish(client):
    resp = client.post("/api/skills/platform/projects/upload", content=YML.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text
    return project_id


def test_new_live_session_resumes_the_users_current_state_not_init(client):
    project_id = _upload_and_publish(client)
    frames = enter_chat(client, project_id)
    assert _info_of(frames)["state"]["key"] == "a"

    assert chat_action(client, session_of(frames), "go")["state"]["key"] == "b"

    created = _created(client, project_id)
    assert created["state"]["key"] == "b"
    assert "task" not in created

    # A brand-new session has no Tracking rows of its own yet — entering
    # it by its own id must still read "b" off the session's own persisted
    # start_state, not fall back to init for lack of a transition to read.
    assert _reopened(client, created["session_id"])["state"]["key"] == "b"


CHATLESS_FINAL_YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        target: crisis\n"
    "  crisis:\n"
    "    contextual-prompt: bye\n"
    "    chat-enabled: false\n"
    "    actions: []\n"
)


def test_new_live_session_from_a_chatless_final_state_still_resumes_there(client):
    resp = client.post("/api/skills/platform/projects/upload", content=CHATLESS_FINAL_YML.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text

    session_id = session_of(enter_chat(client, project_id))
    chat_action(client, session_id, "go")

    created = _created(client, project_id)

    assert _reopened(client, created["session_id"])["state"]["key"] == "crisis"


def test_new_test_session_still_restarts_at_init_every_time(client, app_db):
    project_id = _upload_and_publish(client)
    resp = client.post(f"/api/skills/platform/projects/{project_id}/test-sessions")
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert first["start_state"] == "a"
    # init-action's task fires as a task, never inside this response.
    assert "task" not in first
    assert [t["payload"]["script"].strip() for t in app_db.list_tasks()] == ["task.send_mail(user.email, 'hi')"]

    assert chat_action(client, first["id"], "go")["state"]["key"] == "b"

    resp = client.post(f"/api/skills/platform/projects/{project_id}/test-sessions")
    assert resp.status_code == 200, resp.text
    second = resp.json()
    assert second["start_state"] == "a"
    assert "task" not in second
    assert [t["payload"]["script"].strip() for t in app_db.list_tasks()] == ["task.send_mail(user.email, 'hi')"] * 2
