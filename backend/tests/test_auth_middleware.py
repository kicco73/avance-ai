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

    def grant(self, username: str, project_name: str) -> None:
        self._accessible.add((username, project_name))

    def user_has_project_access(self, username: str, project_name: str) -> bool:
        return (username, project_name) in self._accessible


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

    @app.get("/api/projects/{project_name}/protected")
    def protected_project(project_name: str):
        return {"project_name": project_name}

    return TestClient(app)


class TestAllowlist:
    def test_the_login_path_is_reachable_without_a_cookie(self, client):
        response = client.get("/api/auth/login")
        assert response.status_code == 200


class TestProtectedRoute:
    def test_no_cookie_is_rejected_with_401(self, client):
        response = client.get("/api/protected")
        assert response.status_code == 401
        assert response.json()["error"]["message"]

    def test_a_cookie_verify_token_rejects_is_401(self, client):
        client.cookies.set(SESSION_COOKIE_NAME, "garbage")
        response = client.get("/api/protected")
        assert response.status_code == 401

    def test_a_valid_cookie_is_accepted_and_sets_session_user(self, client, identity):
        client.cookies.set(SESSION_COOKIE_NAME, "good-token")
        response = client.get("/api/protected")
        assert response.status_code == 200
        assert response.json()["user"] == identity.email


class TestProjectOwnershipGate:
    """A plain 'user' (identity's own default role — see the identity
    fixture) only ever reaches a {project_name}-scoped route for a
    project Db.user_has_project_access says they belong to — checked
    once here for every such route at once, rather than in each
    controller method (see ProjectService.resolve_invite_link for how a
    project is granted in the first place)."""

    def test_a_user_without_access_is_forbidden(self, client):
        client.cookies.set(SESSION_COOKIE_NAME, "good-token")
        response = client.get("/api/projects/proj-a/protected")
        assert response.status_code == 403

    def test_a_user_with_access_is_allowed(self, client, identity, fake_db):
        fake_db.grant(identity.email, "proj-a")
        client.cookies.set(SESSION_COOKIE_NAME, "good-token")
        response = client.get("/api/projects/proj-a/protected")
        assert response.status_code == 200
        assert response.json()["project_name"] == "proj-a"

    def test_a_non_user_role_bypasses_the_check_entirely(self, client, fake_db):
        admin = AuthenticatedUser(provider_user_id="sub-2", email="admin@example.com", name="Admin", picture_url=None, role="admin")
        client.app.state.auth_service = _FakeAuthService({"admin-token": admin})
        client.cookies.set(SESSION_COOKIE_NAME, "admin-token")

        response = client.get("/api/projects/proj-a/protected")

        assert response.status_code == 200
