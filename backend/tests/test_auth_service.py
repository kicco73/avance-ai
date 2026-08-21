from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from auth.auth_provider import AuthenticatedUser, AuthProvider
from auth.auth_service import AuthService
from auth.errors import AuthError
from config import AuthProviderConfig

pytestmark = pytest.mark.contract

JWT_SECRET = "test-secret"


class _FakeProvider(AuthProvider):
    """Verifies whatever credential it's configured to accept, raising
    AuthError for anything else — stands in for a real GoogleAuthProvider
    without a network call."""

    def __init__(self, accepted_credential: str, identity: AuthenticatedUser) -> None:
        self._accepted_credential = accepted_credential
        self._identity = identity

    def verify(self, credential: str) -> AuthenticatedUser:
        if credential != self._accepted_credential:
            raise AuthError("Invalid credential.")
        return self._identity


def _auth_service(db, provider: _FakeProvider) -> AuthService:
    service = AuthService(db, JWT_SECRET, [AuthProviderConfig(driver="google", key="unused", ui_label="Google")])
    # AuthService builds its own provider instances from config (see its
    # own _PROVIDER_CLASSES registry) — swapped out here for the fake,
    # since building a real GoogleAuthProvider means a real client id.
    service._providers["google"] = provider
    return service


@pytest.fixture
def identity() -> AuthenticatedUser:
    return AuthenticatedUser(provider_user_id="sub-123", email="alice@example.com", name="Alice")


@pytest.fixture
def provider(identity) -> _FakeProvider:
    return _FakeProvider("good-credential", identity)


@pytest.fixture
def auth_service(db, provider) -> AuthService:
    return _auth_service(db, provider)


class TestLogin:
    def test_unknown_provider_raises_value_error(self, auth_service):
        with pytest.raises(ValueError):
            auth_service.login("not-a-real-provider", "whatever")

    def test_invalid_credential_raises_auth_error(self, auth_service):
        with pytest.raises(AuthError):
            auth_service.login("google", "bad-credential")

    def test_valid_credential_creates_a_user_and_returns_a_token(self, db, auth_service, identity):
        token = auth_service.login("google", "good-credential")

        assert isinstance(token, str)
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user = db.get_user_by_id(payload["user_id"])
        assert user is not None
        assert user["email"] == identity.email
        assert user["provider_user_id"] == identity.provider_user_id
        assert payload["provider"] == "google"

    def test_a_second_login_from_the_same_identity_reuses_the_same_user(self, db, auth_service):
        first_token = auth_service.login("google", "good-credential")
        second_token = auth_service.login("google", "good-credential")

        first_user_id = jwt.decode(first_token, JWT_SECRET, algorithms=["HS256"])["user_id"]
        second_user_id = jwt.decode(second_token, JWT_SECRET, algorithms=["HS256"])["user_id"]
        assert first_user_id == second_user_id

    def test_login_updates_last_login(self, db, auth_service):
        token = auth_service.login("google", "good-credential")
        user_id = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])["user_id"]

        user = db.get_user_by_id(user_id)
        assert user is not None
        assert user["last_login"] is not None


class TestVerifyToken:
    def test_a_token_issued_by_login_verifies_back_to_the_same_identity(self, auth_service, identity):
        token = auth_service.login("google", "good-credential")

        verified = auth_service.verify_token(token)

        assert verified is not None
        assert verified.email == identity.email
        assert verified.provider_user_id == identity.provider_user_id
        assert verified.name == identity.name

    def test_a_garbage_token_returns_none(self, auth_service):
        assert auth_service.verify_token("not-a-real-jwt") is None

    def test_a_token_signed_with_a_different_secret_returns_none(self, auth_service):
        forged = jwt.encode({"user_id": 1, "provider": "google"}, "wrong-secret", algorithm="HS256")
        assert auth_service.verify_token(forged) is None

    def test_an_expired_token_returns_none(self, auth_service):
        expired = jwt.encode(
            {"user_id": 1, "provider": "google", "exp": datetime.now(timezone.utc) - timedelta(days=1)},
            JWT_SECRET, algorithm="HS256",
        )
        assert auth_service.verify_token(expired) is None

    def test_a_token_naming_a_user_that_no_longer_exists_returns_none(self, auth_service):
        token = jwt.encode({"user_id": 999999, "provider": "google"}, JWT_SECRET, algorithm="HS256")
        assert auth_service.verify_token(token) is None
