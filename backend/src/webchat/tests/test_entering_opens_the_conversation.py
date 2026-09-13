"""Entering a fresh conversation makes the automaton speak, and what it
says reaches the browser.

Nothing covered this end to end. `tests/test_session_enter.py` drives the
listener directly and sees the opening turn on the Bus; `enter_chat` stops
reading at `state.buttons`, which is the last frame that answers the
request. Between the two there was no test that a person who opens a chat
is greeted — the one thing every conversation does, and the one that broke.
"""
from __future__ import annotations

import pytest

from conftest import _frame_deadline, chat_socket, turn_frame_seconds

pytestmark = pytest.mark.regression


def _entering(client, project_id: str, session_type: str = "live") -> list[dict]:
    frames: list[dict] = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({"type": "session.enter", "project_id": project_id, "session_type": session_type})
            while True:
                frames.append(ws.receive_json())
                if frames[-1]["type"] in ("output.text", "output.error", "session.blocked"):
                    return frames


def test_the_opening_message_arrives_on_the_socket(client, hello_project):
    frames = _entering(client, hello_project)

    kinds = [frame["type"] for frame in frames]
    assert kinds[:3] == ["session.info", "session.messages", "state.buttons"], kinds
    assert kinds[-1] == "output.text", kinds
    assert frames[-1]["text"].strip(), frames[-1]


def test_it_is_written_in_pieces_like_any_other_answer(client, hello_project):
    """No difference to whoever is reading between a greeting and a
    reply, so there is none on the wire: the empty piece that opens the
    bubble comes first, and the whole message last."""
    frames = _entering(client, hello_project)

    streamed = [frame for frame in frames if frame["type"] == "output.text_stream"]
    assert streamed, [frame["type"] for frame in frames]
    assert streamed[0]["text"] == ""
    assert "".join(frame["text"] for frame in streamed) == frames[-1]["text"]


def test_entering_it_again_greets_nobody_twice(client, hello_project):
    """The same conversation, entered a second time: what was said comes
    back on `session.messages` and the automaton stays quiet."""
    opened = _entering(client, hello_project)

    frames: list[dict] = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({"type": "session.enter", "project_id": hello_project, "session_type": "live"})
            while True:
                frames.append(ws.receive_json())
                if frames[-1]["type"] == "state.buttons":
                    break
            ws.send_json({"type": "input.text", "text": "hello there", "session_id": frames[0]["session_id"]})
            while frames[-1]["type"] != "output.text":
                frames.append(ws.receive_json())

    said = next(frame for frame in frames if frame["type"] == "session.messages")
    assert [message["content"] for message in said["messages"]] == [opened[-1]["text"]]
    # The only answer written after entering again is the one to what was
    # typed: an opening would have arrived before it.
    answers = [frame for frame in frames if frame["type"] == "output.text"]
    assert len(answers) == 1, [frame["type"] for frame in frames]


def test_a_new_session_asked_for_outright_is_greeted_too(client, hello_project):
    """`session.create` used to fall through the queue of requests as if
    it were something typed, and was answered with "Message cannot be
    empty" — the conversation was made and then never spoke. Entering and
    creating differ in which session you end up in, in nothing else."""
    first = _entering(client, hello_project)

    frames: list[dict] = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            ws.send_json({"type": "session.create", "project_id": hello_project, "session_type": "live"})
            while True:
                frames.append(ws.receive_json())
                if frames[-1]["type"] in ("output.text", "output.error", "session.blocked"):
                    break

    kinds = [frame["type"] for frame in frames]
    assert kinds[:3] == ["session.info", "session.messages", "state.buttons"], kinds
    assert kinds[-1] == "output.text", kinds
    assert frames[0]["session_id"] != first[0]["session_id"]
    assert frames[-1]["text"] == first[-1]["text"]
