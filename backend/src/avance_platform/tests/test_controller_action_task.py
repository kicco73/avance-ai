"""Firing an action fires the action's own task (its snippets reach the
browser over the websocket as a background ActionTask, never in this
response) and, separately, its own on-exit script — including any
chat.* calls, pushed synchronously, in this same request, never
hibernated. Both are the action's own, not anything read off the
destination state — two actions landing on the same state can disagree
on either.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from conftest import (
    _frame_deadline, chat_action, chat_action_frames, chat_socket, enter_chat, parse_sse_result,
    session_of, turn_frame_seconds,
)

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


YML_PROGRESS = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go-loud\n"
    "        target: b\n"
    "        on-exit: chat.progress('Uploading', 42)\n"
    "  b:\n"
    "    contextual-prompt: there\n"
    "    actions:\n"
    "      - name: back\n"
    "        target: a\n"
)


def test_manual_action_pushes_its_on_exits_own_output_progress(client, app_db):
    """chat.progress publishes a real bus message, delivered on the
    per-session "who is watching" path (like output.chart) — never the
    identity-wide ui.progress. That path only reaches a connection that
    has actually entered this session (session.enter watches it), so
    unlike the ui.notification-based test above, this one keeps a single
    socket open across session.enter and the action instead of using
    chat_action_frames' own fresh, never-entered connection. session.enter
    also kicks off its own background "speaks first" greeting (history is
    empty) whose frames can interleave with the click's own — so this
    reads until state b's own buttons ('back') actually settle, not just
    until any state.buttons shows up."""
    resp = client.post("/api/skills/platform/projects/upload", content=YML_PROGRESS.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.post(f"/api/core/projects/{project_id}/activate")
    assert resp.status_code == 200, resp.text
    resp = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text

    def settled_on_b(frames: list[dict]) -> bool:
        return any(
            frame["type"] == "state.buttons" and any(action["name"] == "back" for action in frame["actions"])
            for frame in frames
        )

    frames = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({"type": "session.enter", "project_id": project_id, "session_type": "live"})
            while not frames or frames[-1]["type"] != "state.buttons":
                frames.append(ws.receive_json())
            session_id = session_of(frames)
            ws.send_json({"type": "input.button", "session_id": session_id, "id": "go-loud"})
            while not settled_on_b(frames):
                frames.append(ws.receive_json())

    progress_frames = [frame for frame in frames if frame["type"] == "output.progress"]
    assert progress_frames == [
        {"type": "output.progress", "session_id": session_id, "project_id": project_id, "title": "Uploading", "percentage": 42},
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


NOTES_YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: show-notes\n"
    "        target: a\n"
    "        on-exit: chat.show(attachment.read('notes.md'))\n"
)


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_on_exit_reads_an_attachment_and_shows_it(client, app_db):
    archive = _zip_of({"index.yml": NOTES_YML, "behaviour/notes.md": "# Notes\n\nhello notes"})
    resp = client.post("/api/skills/platform/projects/upload", content=archive, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    assert client.post(f"/api/core/projects/{project_id}/activate").status_code == 200
    resp = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text
    session_id = session_of(enter_chat(client, project_id))

    frames = chat_action_frames(client, session_id, "show-notes")

    assert [frame for frame in frames if frame["type"] == "ui.notification"] == [
        {"type": "ui.notification", "task": 'show("# Notes\\n\\nhello notes")'},
    ]
    assert app_db.list_tasks() == []


def test_on_exit_attachment_read_of_a_missing_file_is_refused_at_build_time():
    from automaton.automaton_builder import AutomatonBuilder

    with pytest.raises(ValueError, match="attachment named 'notes.md' not found"):
        AutomatonBuilder().build({"index.yml": NOTES_YML})
