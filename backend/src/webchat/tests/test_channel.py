"""A conversation entered over the browser socket is stamped with the
channel this package *is* (see BusChannel.owned_by and webchat/skill.py),
and the core routes that address a session by id still ask for no channel
at all.

Nothing here goes through AuthMiddleware — the `app` fixture wires none —
and nothing needs to: the socket builds the sender's own context from the
cookie it was handed, and the channel comes from whoever claimed the
socket, never from whatever the suite happens to have set.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import enter_chat, session_of

pytestmark = pytest.mark.contract


def _info_of(frames: list[dict]) -> dict:
    return next(frame for frame in frames if frame["type"] == "session.info")


def test_a_conversation_entered_from_the_chat_window_is_stamped_with_its_channel(
    client: TestClient, hello_project: str
):
    info = _info_of(enter_chat(client, hello_project))

    assert info["channel"] == "webchat"
    assert info["session_type"] == "live"


def test_the_core_history_of_the_same_session_needs_no_channel(client: TestClient, hello_project: str):
    """The counterpart, and the reason turn/session_controller.py has a
    history route of its own. read_history returns what is already there
    and opens nothing, so it never reaches the write admission gate and
    never asks who is speaking — which is what lets the editor, the
    labelling screens, the app store's preview and the testing skill read
    a history without being a channel. The session below is opened by the
    chat window and read, over plain HTTP, straight after."""
    session_id = session_of(enter_chat(client, hello_project))

    response = client.get(f"/api/core/sessions/{session_id}/history")

    assert response.status_code == 200
