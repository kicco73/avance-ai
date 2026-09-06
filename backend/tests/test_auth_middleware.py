"""AuthMiddleware's own request-level contract, independent of the real
AuthService/main.py wiring — a minimal FastAPI app with the middleware
attached and a fake AuthService bridged onto app.state, same as
main.py's own lifespan does for the real one.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.auth_middleware import AuthMiddleware
from auth.auth_provider import AuthenticatedUser
from auth.auth_service import SESSION_COOKIE_NAME
from session import Session

pytestmark = pytest.mark.contract


class _FakeAuthService:
    def __init__(self, valid_tokens: dict[str, AuthenticatedUser]) -> None:
        self._valid_tokens = valid_tokens

    def verify_token(self, token):
        return self._valid_tokens.get(token)


class _FakeDb:
    def __init__(self) -> None:
        self._accessible: set[tuple[str, str]] = set()

    def grant(self, username: str, project_id: str) -> None:
        self._accessible.add((username, project_id))

    def user_has_project_access(self, username: str, project_id: str) -> bool:
        return (username, project_id) in self._accessible


@pytest.fixture(autouse=True)
def _restore_session_user():
    previous = Session().user
    yield
    Session().user = previous


@pytest.fixture
def identity() -> AuthenticatedUser:
    return AuthenticatedUser(provider_user_id="sub-1", email="alice@example.com", name="Alice", picture_url=None)


@pytest.fixture
def fake_db() -> _FakeDb:
    return _FakeDb()


@pytest.fixture
def client(identity, fake_db) -> TestClient:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.state.auth_service = _FakeAuthService({"good-token": identity})
    app.state.db = fake_db

    @app.get("/api/auth/login")
    def login_stub():
        return {"allowlisted": True}

    login_stub.__required_role__ = None

    @app.get("/api/protected")
    def protected():
        return {"user": Session().user}

    @app.get("/api/projects/{project_id}/protected")
    def protected_project(project_id: str):
        return {"project_id": project_id}

    return TestClient(app)


def test_an_allowlisted_path_needs_no_cookie_while_a_protected_one_accepts_only_a_verifiable_token(client, identity):
    assert client.get("/api/auth/login").status_code == 200

    no_cookie = client.get("/api/protected")
    assert no_cookie.status_code == 401
    assert no_cookie.json()["error"]["message"]

    client.cookies.set(SESSION_COOKIE_NAME, "garbage")
    assert client.get("/api/protected").status_code == 401

    client.cookies.set(SESSION_COOKIE_NAME, "good-token")
    response = client.get("/api/protected")
    assert response.status_code == 200
    assert response.json()["user"] == identity.email


def test_a_project_scoped_route_is_gated_on_access_for_a_plain_user_and_open_to_every_other_role(client, identity, fake_db):
    """A plain 'user' (identity's own default role) only ever reaches a
    {project_id}-scoped route for a project Db.user_has_project_access
    says they belong to — checked once here for every such route at once,
    rather than in each controller method (see
    ProjectService.resolve_invite_link for how access is granted)."""
    client.cookies.set(SESSION_COOKIE_NAME, "good-token")
    assert client.get("/api/projects/proj-a/protected").status_code == 403

    fake_db.grant(identity.email, "proj-a")
    response = client.get("/api/projects/proj-a/protected")
    assert response.status_code == 200
    assert response.json()["project_id"] == "proj-a"

    admin = AuthenticatedUser(provider_user_id="sub-2", email="admin@example.com", name="Admin", picture_url=None, role="admin")
    client.app.state.auth_service = _FakeAuthService({"admin-token": admin})
    client.cookies.set(SESSION_COOKIE_NAME, "admin-token")

    assert client.get("/api/projects/proj-b/protected").status_code == 200
