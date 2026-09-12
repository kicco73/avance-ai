"""Every route of the chat window speaks on the chat window's channel,
and says so itself.

AuthMiddleware used to say it for them — and for every other
authenticated HTTP request in the system, the editor's included. These
tests run their requests inside a context of their own, so the suite's
own WebSession().channel default (see conftest's _default_session_user)
cannot stand in for what the controller actually declares.
"""
from __future__ import annotations

import contextvars

import pytest
from fastapi.testclient import TestClient

from system.web_session import WebSession

pytestmark = pytest.mark.contract


def _in_a_fresh_context(call):
    """Identity but no channel — the `app` fixture wires no AuthMiddleware
    (see its docstring), so the identity has to be restated here; the
    channel deliberately is not."""
    def run():
        WebSession().user = "user"
        WebSession().role = "supervisor"
        return call()

    return contextvars.Context().run(run)


def test_a_session_opened_from_the_chat_window_is_stamped_with_its_channel(
    client: TestClient, hello_project: str
):
    session = _in_a_fresh_context(lambda: client.get("/api/skills/webchat/sessions/current").json())

    assert session["channel"] == "webchat"
    assert session["type"] == "live"


def test_an_explicit_new_session_is_stamped_too(client: TestClient, hello_project: str):
    session = _in_a_fresh_context(lambda: client.post("/api/skills/webchat/sessions").json())

    assert session["channel"] == "webchat"


def test_reading_history_declares_the_channel_as_well(client: TestClient, hello_project: str):
    """Not a formality: get_messages calls open_if_needed, which runs a
    project's opening message as a real turn — through the same write
    admission gate a typed message goes through. A read here is not a
    read, which is why the declaration is on the whole controller and not
    on the three methods that obviously need it."""
    session = _in_a_fresh_context(lambda: client.get("/api/skills/webchat/sessions/current").json())

    response = _in_a_fresh_context(
        lambda: client.get(f"/api/skills/webchat/sessions/{session['id']}/messages")
    )

    assert response.status_code == 200


def test_the_core_history_of_the_same_session_needs_no_channel(client: TestClient, hello_project: str):
    """The counterpart, and the reason turn/session_controller.py has a
    history route of its own. read_history returns what is already there
    and opens nothing, so it never reaches the write admission gate and
    never asks who is speaking — which is what lets the editor, the
    labelling screens, the app store's preview and the testing skill read
    a history without being a channel. The session below is opened by the
    chat window and read, with no channel at all, straight after."""
    session = _in_a_fresh_context(lambda: client.get("/api/skills/webchat/sessions/current").json())

    response = _in_a_fresh_context(
        lambda: client.get(f"/api/core/sessions/{session['id']}/history")
    )

    assert response.status_code == 200


def test_a_manual_action_is_admitted_on_the_chat_window_s_channel(
    client: TestClient, hello_project: str
):
    session = _in_a_fresh_context(lambda: client.get("/api/skills/webchat/sessions/current").json())

    response = _in_a_fresh_context(
        lambda: client.post(
            f"/api/skills/webchat/sessions/{session['id']}/actions", json={"action_name": "chat"}
        )
    )

    assert response.status_code != 409
