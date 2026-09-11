"""Session().channel has no default (unlike Session().user/role, it used
to silently resolve to 'native-chat') — reading it outside a request
context raises the same way user/role already do, and every real entry
point that needs it declares it first.

Nothing in core declares one. AuthMiddleware used to, for every
authenticated HTTP request, which meant the editor's own routes opened
sessions claiming to have come from the chat window. Each channel names
itself now, and the session types that are not a conversation with
anybody never ask (see SessionTypeStrategy.caller_channel).
"""
from __future__ import annotations

import contextvars

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.auth_middleware import AuthMiddleware
from auth.auth_provider import AuthenticatedUser
from auth.auth_service import SESSION_COOKIE_NAME
from automaton.automaton import Action, Automaton, State
from turn.sessions.session_manager import SessionManager
from turn.sessions.session_type_strategy import get_session_type_strategy
from system.session import Session

pytestmark = pytest.mark.contract

LIVE = get_session_type_strategy('live')
TEST = get_session_type_strategy('test')


def test_reading_channel_outside_a_request_context_raises():
    ctx = contextvars.Context()

    with pytest.raises(RuntimeError, match="outside a request context"):
        ctx.run(lambda: Session().channel)


class _FakeAuthService:
    def __init__(self, valid_tokens: dict[str, AuthenticatedUser]) -> None:
        self._valid_tokens = valid_tokens

    def verify_token(self, token):
        return self._valid_tokens.get(token)


class _FakeDb:
    def user_has_project_access(self, username: str, project_name: str) -> bool:
        return True


def test_the_middleware_authenticates_and_declares_no_channel():
    """It knows who you are and what you may do. It does not know what
    you are speaking on, and it used to answer anyway."""
    identity = AuthenticatedUser(provider_user_id="sub-1", email="alice@example.com", name="Alice", picture_url=None)
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.state.auth_service = _FakeAuthService({"good-token": identity})
    app.state.db = _FakeDb()

    @app.get("/api/protected")
    def protected():
        try:
            return {"user": Session().user, "channel": Session().channel}
        except RuntimeError:
            return {"user": Session().user, "channel": "<never declared>"}

    def call():
        client = TestClient(app)
        client.cookies.set(SESSION_COOKIE_NAME, "good-token")
        return client.get("/api/protected")

    # In a context of its own, so the suite's own default (see conftest's
    # _default_session_user) cannot stand in for what the middleware did
    # or did not set.
    response = contextvars.Context().run(call)

    assert response.status_code == 200
    assert response.json()["user"] == "alice@example.com"
    assert response.json()["channel"] == "<never declared>"


def _automaton() -> Automaton:
    init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[])
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="",
        signals=[],
        general_attachments={},
        autotracking_on_ai_message=False,
    )


class _FakeProjectService:
    def __init__(self, automaton: Automaton) -> None:
        self._automaton = automaton

    def get_automaton_and_state(self, project_name, type='live', username=None):
        return self._automaton, self._automaton.states["a"]

    def get_published_revision(self, project_name):
        return 0

    def get_draft_revision(self, project_name):
        return 0

    def get_automaton(self, project_name, revision):
        return self._automaton


def test_creating_a_live_session_without_a_channel_fails(db):
    """A caller that never declared one (a job, or any HTTP route that is
    not a channel) must fail loudly instead of silently stamping the
    session 'native-chat' — the old column default this used to fall
    back on."""
    db.ensure_project("proj")
    db.publish_project("proj")
    manager = SessionManager(db)
    project_service = _FakeProjectService(_automaton())
    ctx = contextvars.Context()

    def job_body():
        return manager.create_session(LIVE, project_service, "user", "proj")

    with pytest.raises(RuntimeError, match="outside a request context"):
        ctx.run(job_body)

    assert db.list_chat_sessions("user", "proj") == []


def test_creating_a_test_session_without_a_channel_is_how_it_is_supposed_to_work(db):
    """The other half, and the reason the middleware could stop guessing:
    a test session is not a conversation with anybody, so it never asks
    who is speaking and is stamped with no channel at all. Every editor
    route that opens one goes through here."""
    db.ensure_project("proj")
    db.publish_project("proj")
    manager = SessionManager(db)
    project_service = _FakeProjectService(_automaton())

    session = contextvars.Context().run(
        lambda: manager.create_session(TEST, project_service, "user", "proj")
    )

    assert session["type"] == "test"
    assert session["channel"] is None
