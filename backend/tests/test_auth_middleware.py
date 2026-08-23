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


@pytest.fixture(autouse=True)
def _restore_session_user():
    previous = Session().user
    yield
    Session().user = previous


@pytest.fixture
def identity() -> AuthenticatedUser:
    return AuthenticatedUser(provider_user_id="sub-1", email="alice@example.com", name="Alice", picture_url=None)


@pytest.fixture
def client(identity) -> TestClient:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.state.auth_service = _FakeAuthService({"good-token": identity})

    @app.get("/api/auth/login")
    def login_stub():
        return {"allowlisted": True}

    login_stub.__required_role__ = None

    @app.get("/api/protected")
    def protected():
        return {"user": Session().user}

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
