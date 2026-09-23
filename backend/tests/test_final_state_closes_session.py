"""A state with no outgoing action is final, and arriving there ends the
conversation: the session is closed by the server and `session.ended` is
the last frame that turn produces — after the state, its choices and
whatever the state had to say — so nothing more is accepted on it (see
docs/BUS.md, `session.ended`). Ending there is what counts, not
arriving: a conversation that starts in such a state closes after its
first turn, and a state to stay in declares a self-loop (`trigger: "True"`).
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
    "    input-processor: ai\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: finish\n"
    "        target: end\n"
    "      - name: go\n"
    "        target: b\n"
    "  b:\n"
    "    input-processor: ai\n"
    "    contextual-prompt: there\n"
    "    actions:\n"
    "      - name: back\n"
    "        target: a\n"
    "  end:\n"
    "    input-processor: ai\n"
    "    contextual-prompt: bye\n"
)


def _tracked_into_end(on_ai_message: bool) -> str:
    return (
        "project:\n  id: proj\n"
        f"  signal-tracking-on-ai-message: {str(on_ai_message).lower()}\n"
        "init-action:\n  target: a\n"
        "states:\n"
        "  a:\n"
        "    input-processor: ai\n"
        "    contextual-prompt: hi\n"
        "    actions:\n"
        "      - name: finish\n"
        "        target: end\n"
        "        trigger: \"True\"\n"
        "  end:\n"
        "    input-processor: ai\n"
        "    contextual-prompt: bye\n"
    )


def _published(client, yml: str = YML) -> str:
    resp = client.post(
        "/api/skills/platform/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"},
    )
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text
    return project_id


def _greeted(ws, project_id: str) -> int:
    """Enters the project's conversation and waits for the greeting its
    state owes on opening, so the exchange sent next is the only turn in
    flight. Returns the session's id."""
    ws.send_json({"type": "session.enter", "project_id": project_id, "session_type": "live"})
    frames: list[dict] = []
    while frames[-1:] == [] or frames[-1]["type"] != "state.buttons" or "output.text" not in [f["type"] for f in frames]:
        frames.append(ws.receive_json())
    return session_of(frames)


def _exchange(client, project_id: str, request: dict, *last: str) -> tuple[int, list[dict]]:
    frames: list[dict] = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            session_id = _greeted(ws, project_id)
            ws.send_json({**request, "session_id": session_id})
            while frames[-1:] == [] or frames[-1]["type"] not in last:
                frames.append(ws.receive_json())
    return session_id, frames


def test_a_turn_that_ends_in_a_final_state_closes_the_session_after_everything_it_said(client, app_db):
    project_id = _published(client)

    session_id, frames = _exchange(
        client, project_id, {"type": "input.button", "id": "finish"}, "session.ended", "output.error",
    )
    kinds = [frame["type"] for frame in frames]

    assert frames[-1] == {"type": "session.ended", "session_id": session_id, "project_id": project_id, "reason": "final-state"}
    assert kinds.index("state.changed") < kinds.index("state.buttons") < kinds.index("session.ended")
    assert next(f for f in frames if f["type"] == "state.changed")["state"]["final"] is True
    assert next(f for f in frames if f["type"] == "state.buttons")["actions"] == []
    session = app_db.get_chat_session(session_id)
    assert session["closed_at"] is not None and session["close_reason"] == "final-state"
    assert chat_turn_error(client, session_id)["code"] == "session_closed"


def _closed_after_its_last_word(app_db, session_id: int, frames: list[dict]) -> None:
    kinds = [frame["type"] for frame in frames]
    assert kinds[-1] == "session.ended" and frames[-1]["reason"] == "final-state"
    assert kinds.index("state.changed") < kinds.index("session.ended")
    assert len(kinds) - 1 - kinds[::-1].index("output.text") < kinds.index("session.ended")
    assert next(f for f in frames if f["type"] == "state.changed")["new_state"] == "end"
    assert app_db.get_chat_session(session_id)["close_reason"] == "final-state"


def test_a_trigger_evaluated_on_the_persons_message_closes_the_session_after_the_reply(client, app_db):
    project_id = _published(client, _tracked_into_end(on_ai_message=False))

    session_id, frames = _exchange(
        client, project_id, {"type": "input.text", "text": "hi"}, "session.ended", "output.error",
    )

    _closed_after_its_last_word(app_db, session_id, frames)
    assert chat_turn_error(client, session_id)["code"] == "session_closed"


def test_a_trigger_evaluated_on_the_ai_message_fires_on_the_greeting_itself_and_closes_after_it(client, app_db):
    project_id = _published(client, _tracked_into_end(on_ai_message=True))

    frames: list[dict] = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({"type": "session.enter", "project_id": project_id, "session_type": "live"})
            while frames[-1:] == [] or frames[-1]["type"] not in ("session.ended", "output.error"):
                frames.append(ws.receive_json())
    session_id = session_of(frames)

    _closed_after_its_last_word(app_db, session_id, frames)
    assert chat_turn_error(client, session_id)["code"] == "session_closed"


def test_a_turn_that_ends_in_a_state_with_a_way_out_leaves_the_session_open(client, app_db):
    project_id = _published(client)

    session_id, frames = _exchange(
        client, project_id, {"type": "input.button", "id": "go"}, "state.buttons", "output.error",
    )

    assert next(f for f in frames if f["type"] == "state.changed")["state"]["final"] is False
    assert app_db.get_chat_session(session_id)["closed_at"] is None


ONE_STATE_YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: only\n"
    "states:\n"
    "  only:\n"
    "    input-processor: ai\n"
    "    contextual-prompt: hi\n"
)

SELF_LOOP_YML = ONE_STATE_YML + (
    "    actions:\n"
    "      - name: again\n"
    "        target: only\n"
    "        trigger: \"True\"\n"
)


def _published_yml(client, yml: str) -> str:
    resp = client.post(
        "/api/skills/platform/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"},
    )
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text
    return project_id


def test_a_one_state_hello_world_says_hello_and_closes(client, app_db):
    project_id = _published_yml(client, ONE_STATE_YML)

    frames: list[dict] = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({"type": "session.enter", "project_id": project_id, "session_type": "live"})
            while frames[-1:] == [] or frames[-1]["type"] not in ("session.ended", "output.error"):
                frames.append(ws.receive_json())
    kinds = [frame["type"] for frame in frames]

    assert kinds[-1] == "session.ended" and frames[-1]["reason"] == "final-state"
    assert kinds.index("output.text") < kinds.index("session.ended")
    session_id = session_of(frames)
    assert app_db.get_chat_session(session_id)["close_reason"] == "final-state"
    assert chat_turn_error(client, session_id, "hi")["code"] == "session_closed"


def test_a_self_loop_that_always_fires_keeps_a_one_state_conversation_open(client, app_db):
    session_id = session_of(enter_chat(client, _published_yml(client, SELF_LOOP_YML)))

    chat_turn(client, session_id, "hi")
    chat_turn(client, session_id, "and again")

    assert app_db.get_chat_session(session_id)["closed_at"] is None


def test_the_new_project_template_is_a_conversation_that_stays_open(client, app_db, hello_project):
    session_id = session_of(enter_chat(client, hello_project))

    chat_turn(client, session_id, "hi")
    chat_turn(client, session_id, "and again")

    assert app_db.get_chat_session(session_id)["closed_at"] is None
