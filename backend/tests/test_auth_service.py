from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from auth.auth_provider import AuthenticatedUser, AuthProvider
from auth.auth_service import AuthService
from auth.errors import AuthError
from config import AuthProviderConfig

pytestmark = pytest.mark.contract


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

    def public_config(self) -> dict:
        return {"client_id": "fake-client-id"}


def _auth_service(db, provider: _FakeProvider) -> AuthService:
    service = AuthService(
        db, [AuthProviderConfig(driver="google", key="unused", ui_label="Google")], token_ttl_in_hours=24 * 7,
    )
    # AuthService builds its own provider instances from config (see its
    # own _PROVIDER_CLASSES registry) — swapped out here for the fake,
    # since building a real GoogleAuthProvider means a real client id.
    service._providers["google"] = provider
    return service


@pytest.fixture
def identity() -> AuthenticatedUser:
    return AuthenticatedUser(provider_user_id="sub-123", email="alice@example.com", name="Alice", picture_url="https://example.com/alice.png")


@pytest.fixture
def provider(identity) -> _FakeProvider:
    return _FakeProvider("good-credential", identity)


@pytest.fixture
def auth_service(db, provider) -> AuthService:
    return _auth_service(db, provider)


@pytest.fixture
def jwt_secret(db, auth_service) -> str:
    return db.get_setting("jwt-secret")


class TestLogin:
    def test_unknown_provider_raises_value_error(self, auth_service):
        with pytest.raises(ValueError):
            auth_service.login("not-a-real-provider", "whatever")

    def test_invalid_credential_raises_auth_error(self, auth_service):
        with pytest.raises(AuthError):
            auth_service.login("google", "bad-credential")

    def test_valid_credential_returns_a_token_without_creating_a_user(self, db, auth_service, identity, jwt_secret):
        """login() deliberately defers user creation to
        complete_registration() (see TestCompleteRegistration below) — a
        first-time identity gets a token, not a User row, so rejecting
        the Terms screen leaves no trace at all."""
        token = auth_service.login("google", "good-credential")

        assert isinstance(token, str)
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        assert payload["email"] == identity.email
        assert payload["provider_user_id"] == identity.provider_user_id
        assert payload["provider"] == "google"
        assert db.get_user_by_id(identity.email) is None

    def test_a_second_login_from_the_same_identity_before_registering_still_creates_no_user(
        self, db, auth_service, identity
    ):
        auth_service.login("google", "good-credential")
        auth_service.login("google", "good-credential")

        assert db.get_user_by_id(identity.email) is None

    def test_login_updates_last_login_for_an_already_registered_user(self, db, auth_service, identity, jwt_secret):
        first_token = auth_service.login("google", "good-credential")
        auth_service.complete_registration(first_token)

        second_token = auth_service.login("google", "good-credential")
        payload = jwt.decode(second_token, jwt_secret, algorithms=["HS256"])
        user = db.get_user_by_id(payload["email"])
        assert user is not None
        assert user["last_login"] is not None


class TestCompleteRegistration:
    def test_accepting_terms_creates_the_user_row(self, db, auth_service, identity):
        token = auth_service.login("google", "good-credential")

        auth_service.complete_registration(token)

        user = db.get_user_by_id(identity.email)
        assert user is not None
        assert user["email"] == identity.email
        assert user["provider_user_id"] == identity.provider_user_id
        assert user["last_login"] is not None

    def test_the_same_token_still_works_after_registering(self, auth_service, identity):
        token = auth_service.login("google", "good-credential")
        auth_service.complete_registration(token)

        verified = auth_service.verify_token(token)

        assert verified is not None
        assert verified.email == identity.email
        assert verified.role is not None

    def test_an_invalid_token_raises_value_error(self, auth_service):
        with pytest.raises(ValueError):
            auth_service.complete_registration("not-a-real-jwt")


class TestVerifyToken:
    def test_a_token_issued_by_login_verifies_to_a_pending_identity(self, auth_service, identity):
        """No User row exists yet right after login() — verify_token
        still resolves the identity (straight off the token), just with
        role=None, rather than treating it as unauthenticated."""
        token = auth_service.login("google", "good-credential")

        verified = auth_service.verify_token(token)

        assert verified is not None
        assert verified.email == identity.email
        assert verified.provider_user_id == identity.provider_user_id
        assert verified.name == identity.name
        assert verified.role is None

    def test_a_token_verifies_to_the_registered_identity_once_terms_are_accepted(self, auth_service, identity):
        token = auth_service.login("google", "good-credential")
        auth_service.complete_registration(token)

        verified = auth_service.verify_token(token)

        assert verified is not None
        assert verified.email == identity.email
        assert verified.role is not None

    def test_a_garbage_token_returns_none(self, auth_service):
        assert auth_service.verify_token("not-a-real-jwt") is None

    def test_a_token_signed_with_a_different_secret_returns_none(self, auth_service):
        forged = jwt.encode({"email": "alice@example.com", "provider": "google"}, "wrong-secret", algorithm="HS256")
        assert auth_service.verify_token(forged) is None

    def test_an_expired_token_returns_none(self, auth_service, jwt_secret):
        expired = jwt.encode(
            {"email": "alice@example.com", "provider": "google", "exp": datetime.now(timezone.utc) - timedelta(days=1)},
            jwt_secret, algorithm="HS256",
        )
        assert auth_service.verify_token(expired) is None
