"""A state with no outgoing action is final, and arriving there ends the
conversation: the session is closed by the server and `session.ended` is
the last frame that turn produces — after the state, its choices and
whatever the state had to say — so nothing more is accepted on it (see
docs/BUS.md, `session.ended`). Only arriving: a conversation that starts
in such a state, as a one-state project's does, is an ordinary chat.
"""
from __future__ import annotations

import pytest

from conftest import (
    _frame_deadline, chat_socket, chat_turn, chat_turn_error, enter_chat, parse_sse_result, session_of,
    turn_frame_seconds,
)

pytestmark = pytest.mark.contract

YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: finish\n"
    "        target: end\n"
    "      - name: go\n"
    "        target: b\n"
    "  b:\n"
    "    contextual-prompt: there\n"
    "    actions:\n"
    "      - name: back\n"
    "        target: a\n"
    "  end:\n"
    "    contextual-prompt: bye\n"
)


def _published(client) -> str:
    resp = client.post(
        "/api/skills/platform/projects/upload", content=YML.encode(), headers={"Content-Type": "application/x-yaml"},
    )
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text
    return project_id


def _action_frames_until(client, session_id: int, action: str, *last: str) -> list[dict]:
    frames: list[dict] = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({"type": "session.enter", "session_id": session_id})
            while ws.receive_json()["type"] != "state.buttons":
                pass
            ws.send_json({"type": "input.button", "session_id": session_id, "id": action})
            while True:
                frames.append(ws.receive_json())
                if frames[-1]["type"] in last:
                    return frames


def test_a_turn_that_ends_in_a_final_state_closes_the_session_after_everything_it_said(client, app_db):
    project_id = _published(client)
    session_id = session_of(enter_chat(client, project_id))

    frames = _action_frames_until(client, session_id, "finish", "session.ended", "output.error")
    kinds = [frame["type"] for frame in frames]

    assert frames[-1] == {"type": "session.ended", "session_id": session_id, "project_id": project_id, "reason": "final-state"}
    assert kinds.index("state.changed") < kinds.index("state.buttons") < kinds.index("session.ended")
    assert next(f for f in frames if f["type"] == "state.changed")["state"]["final"] is True
    assert next(f for f in frames if f["type"] == "state.buttons")["actions"] == []
    session = app_db.get_chat_session(session_id)
    assert session["closed_at"] is not None and session["close_reason"] == "final-state"
    assert chat_turn_error(client, session_id)["code"] == "session_closed"


def test_a_turn_that_ends_in_a_state_with_a_way_out_leaves_the_session_open(client, app_db):
    project_id = _published(client)
    session_id = session_of(enter_chat(client, project_id))

    frames = _action_frames_until(client, session_id, "go", "state.buttons", "output.error")

    assert next(f for f in frames if f["type"] == "state.changed")["state"]["final"] is False
    assert app_db.get_chat_session(session_id)["closed_at"] is None


def test_a_conversation_that_starts_in_a_final_state_is_an_ordinary_chat(client, app_db, hello_project):
    session_id = session_of(enter_chat(client, hello_project))

    chat_turn(client, session_id, "hi")
    chat_turn(client, session_id, "and again")

    assert app_db.get_chat_session(session_id)["closed_at"] is None
