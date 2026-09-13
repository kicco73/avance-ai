from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import _frame_deadline, chat_socket, session_of, turn_frame_seconds


def _until(ws, frames: list[dict], *types: str) -> list[dict]:
    while True:
        frames.append(ws.receive_json())
        if frames[-1]["type"] in types:
            return frames


def new_chat(client: TestClient, project_id: str, session_type: str = "live") -> int:
    """A conversation made regardless of whatever one was active — what
    `POST /api/skills/webchat/sessions` used to do (see bus.SESSION_CREATE)."""
    frames: list[dict] = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({
                "type": "session.create", "project_id": project_id, "session_type": session_type,
            })
            return session_of(_until(ws, frames, "state.buttons", "session.blocked"))


def end_chat(client: TestClient, session_id: int) -> None:
    """The person closing that conversation — what `POST
    /api/core/sessions/{id}/close` used to do. Entering it first is what
    makes this socket one of its watchers, so the `session.ended` this
    waits on reaches here (see bus.SESSION_ENDED)."""
    frames: list[dict] = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({"type": "session.enter", "session_id": session_id})
            _until(ws, frames, "state.buttons", "session.blocked")
            ws.send_json({"type": "session.terminate", "session_id": session_id})
            _until(ws, frames, "session.ended")
