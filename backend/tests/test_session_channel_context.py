"""Session().channel has no default (unlike Session().user/role, it used
to silently resolve to 'native-chat') — reading it outside a request
context now raises the same way user/role already do, and every real
entry point that needs it sets it explicitly first.
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
from chat.channels import NATIVE_CHAT
from chat.session_manager import ChatSessionManager
from chat.session_type_strategy import get_session_type_strategy
from session import Session

pytestmark = pytest.mark.contract

LIVE = get_session_type_strategy('live')


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


def test_the_middleware_sets_native_chat():
    identity = AuthenticatedUser(provider_user_id="sub-1", email="alice@example.com", name="Alice", picture_url=None)
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.state.auth_service = _FakeAuthService({"good-token": identity})
    app.state.db = _FakeDb()

    @app.get("/api/protected")
    def protected():
        return {"channel": Session().channel}

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, "good-token")

    response = client.get("/api/protected")

    assert response.status_code == 200
    assert response.json()["channel"] == NATIVE_CHAT


def _automaton() -> Automaton:
    init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[])
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="",
        signals=[],
        attachments={},
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


def test_create_chat_session_from_a_job_without_a_channel_set_fails(db):
    """A job that never went through AuthMiddleware/WhatsAppService's own
    impersonate (so Session().channel was never set) must fail loudly
    instead of silently stamping the session 'native-chat' — the old
    ContextVar default this used to fall back on."""
    db.ensure_project("proj")
    db.publish_project("proj")
    manager = ChatSessionManager(db)
    project_service = _FakeProjectService(_automaton())
    ctx = contextvars.Context()

    def job_body():
        return manager.create_session(LIVE, project_service, "user", "proj")

    with pytest.raises(RuntimeError, match="outside a request context"):
        ctx.run(job_body)

    assert db.list_chat_sessions("user", "proj") == []
