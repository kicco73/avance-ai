from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from auth.auth_provider import AuthenticatedUser, AuthProvider
from auth.auth_service import AuthService
from auth.errors import AuthError
from config import AuthProviderConfig
from project.project_service import ProjectService

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


def _auth_service(db, provider: _FakeProvider, project_service: ProjectService) -> AuthService:
    service = AuthService(
        db, [AuthProviderConfig(driver="google", key="unused", ui_label="Google")],
        token_ttl_in_hours=24 * 7, project_service=project_service,
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
def project_service(db) -> ProjectService:
    return ProjectService(db)


@pytest.fixture
def auth_service(db, provider, project_service) -> AuthService:
    return _auth_service(db, provider, project_service)


@pytest.fixture
def jwt_secret(db, auth_service) -> str:
    return db.get_setting("jwt-secret")


# Registration is invite-only (see AuthService.complete_registration) —
# every test that needs it to actually succeed has to hand it a real
# Invite row's own code.
@pytest.fixture
def invite_code(db, project_service) -> str:
    db.ensure_project("invite-project")
    invite = project_service.create_invite("invite-project", created_by=None)
    return invite["code"]


def _alice(db) -> None:
    db.get_or_create_user("google", "sub-1", "alice@example.com", "Alice", None)


class TestLogin:
    def test_an_unknown_provider_is_a_value_error_and_an_invalid_credential_an_auth_error(self, auth_service):
        with pytest.raises(ValueError):
            auth_service.login("not-a-real-provider", "whatever")
        with pytest.raises(AuthError):
            auth_service.login("google", "bad-credential")

    def test_a_valid_credential_returns_a_token_creating_no_user_until_registration_then_updates_last_login(
        self, db, auth_service, identity, jwt_secret, invite_code
    ):
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

        auth_service.login("google", "good-credential")
        assert db.get_user_by_id(identity.email) is None

        auth_service.complete_registration(token, invite_code)
        second_token = auth_service.login("google", "good-credential")
        payload = jwt.decode(second_token, jwt_secret, algorithms=["HS256"])
        user = db.get_user_by_id(payload["email"])
        assert user is not None
        assert user["last_login"] is not None


class TestCompleteRegistration:
    def test_a_valid_invite_code_creates_the_user_keeps_the_token_valid_and_records_the_redemption(
        self, db, auth_service, identity, invite_code
    ):
        """The one door open: arriving via a "share project" invite link
        (see shareLink.js/useAppBoot.js) — recognizable by a code that
        resolves to a real, unexpired, still-available Invite. The other
        half (UserProjectMixin.record_invite_redemption) stamps invite_id/
        invite_timestamp on the new (user, project) row, which is also how
        count_invite_redemptions knows this invite's own cardinality."""
        token = auth_service.login("google", "good-credential")

        auth_service.complete_registration(token, invite_code)

        user = db.get_user_by_id(identity.email)
        assert user is not None
        assert user["email"] == identity.email
        assert user["provider_user_id"] == identity.provider_user_id
        assert user["last_login"] is not None

        verified = auth_service.verify_token(token)
        assert verified is not None
        assert verified.email == identity.email
        assert verified.role is not None

        invite = db.get_invite_by_code(invite_code)
        assert db.count_invite_redemptions(invite.id) == 1

    def test_an_invalid_token_a_missing_invite_an_unresolvable_or_expired_one_are_all_refused(
        self, db, auth_service, identity
    ):
        """The old self-service behavior — a fresh Google sign-in
        registering itself with no invite context at all — is exactly
        what's now forbidden."""
        with pytest.raises(ValueError):
            auth_service.complete_registration("not-a-real-jwt")

        db.ensure_project("expired-project")
        db.create_invite("EXPIRED", "expired-project", None, datetime.utcnow() - timedelta(days=1), max_shares=3)
        token = auth_service.login("google", "good-credential")

        with pytest.raises(PermissionError):
            auth_service.complete_registration(token)
        with pytest.raises(PermissionError):
            auth_service.complete_registration(token, "no-such-code")
        with pytest.raises(PermissionError):
            auth_service.complete_registration(token, "EXPIRED")

        assert db.get_user_by_id(identity.email) is None

    def test_an_invite_that_already_reached_its_max_shares_is_refused(self, db, provider, identity):
        """A second identity trying the same one-share invite after the
        first already redeemed it."""
        db.ensure_project("maxed-project")
        maxed_service = ProjectService(db, invite_max_shares=1)
        invite = maxed_service.create_invite("maxed-project", created_by=None)
        service = _auth_service(db, provider, maxed_service)
        first_token = service.login("google", "good-credential")
        service.complete_registration(first_token, invite["code"])

        second_provider = _FakeProvider(
            "second-credential",
            AuthenticatedUser(provider_user_id="sub-456", email="bob@example.com", name="Bob", picture_url=None),
        )
        second_service = _auth_service(db, second_provider, maxed_service)
        second_token = second_service.login("google", "second-credential")

        with pytest.raises(PermissionError):
            second_service.complete_registration(second_token, invite["code"])
        assert db.get_user_by_id("bob@example.com") is None


class TestRegisterViaWhatsapp:
    def test_a_valid_invite_code_creates_a_whatsapp_user_activates_the_project_and_records_the_redemption(
        self, db, auth_service, invite_code
    ):
        project_name = auth_service.register_via_whatsapp("34600000001", invite_code)

        assert project_name == "invite-project"
        user = db.get_user_by_id("34600000001")
        assert user is not None
        assert user["email"] is None
        assert user["provider"] == "whatsapp"
        assert db.get_user_by_whatsapp_phone_number("34600000001")["id"] == "34600000001"
        assert db.get_active_project_id("34600000001") == "invite-project"
        assert db.count_invite_redemptions(db.get_invite_by_code(invite_code).id) == 1

    def test_an_unknown_code_is_refused_with_the_same_message_as_the_web(self, db, auth_service):
        with pytest.raises(PermissionError):
            auth_service.register_via_whatsapp("34600000001", "no-such-code")

        assert db.get_user_by_id("34600000001") is None


class TestSetWhatsAppPhoneNumber:
    def test_sets_a_digits_only_number_with_no_collision(self, db, auth_service):
        _alice(db)

        with pytest.raises(ValueError, match="digits only"):
            auth_service.set_whatsapp_phone_number("alice@example.com", "abc123")

        result = auth_service.set_whatsapp_phone_number("alice@example.com", "34600000001")
        assert result["whatsapp_phone_number"] == "34600000001"

    def test_a_collision_as_a_regular_user_reports_that_unification_needs_an_admin_and_cannot_be_forced(
        self, db, auth_service, invite_code
    ):
        _alice(db)
        db.get_or_create_user("google", "sub-2", "bob@example.com", "Bob", None)
        auth_service.set_whatsapp_phone_number("bob@example.com", "34600000001")

        result = auth_service.set_whatsapp_phone_number("alice@example.com", "34600000001")
        assert result["merge_required"] is True
        assert result["merge_allowed"] is False
        assert result["existing_account_id"] == "bob@example.com"
        assert result["existing_account_provider"] == "google"
        assert db.get_user_by_email("alice@example.com")["whatsapp_phone_number"] is None
        assert db.get_user_by_email("bob@example.com")["whatsapp_phone_number"] == "34600000001"

        auth_service.register_via_whatsapp("34600000002", invite_code)
        result = auth_service.set_whatsapp_phone_number("alice@example.com", "34600000002")
        assert result["merge_required"] is True
        assert result["merge_allowed"] is False
        assert result["existing_account_provider"] == "whatsapp"

        with pytest.raises(PermissionError, match="requires admin privileges"):
            auth_service.set_whatsapp_phone_number("alice@example.com", "34600000002", confirm_merge=True)
        assert db.get_user_by_email("alice@example.com")["whatsapp_phone_number"] is None
        assert db.get_user_by_id("34600000002") is not None

    def test_an_admin_is_asked_to_confirm_then_the_merge_moves_the_number_deleting_the_absorbed_account_with_its_sessions_reassigned(
        self, db, auth_service, invite_code
    ):
        _alice(db)
        auth_service.register_via_whatsapp("34600000001", invite_code)
        session_id = db.create_chat_session("34600000001", "invite-project", 0)

        result = auth_service.set_whatsapp_phone_number("alice@example.com", "34600000001", role="admin")
        assert result["merge_required"] is True
        assert result["merge_allowed"] is True
        assert result["existing_account_id"] == "34600000001"
        assert result["existing_account_session_count"] == 1
        assert result["existing_account_created_at"] is not None
        assert db.get_user_by_email("alice@example.com")["whatsapp_phone_number"] is None
        assert db.get_user_by_id("34600000001") is not None

        result = auth_service.set_whatsapp_phone_number("alice@example.com", "34600000001", confirm_merge=True, role="admin")
        assert result["whatsapp_phone_number"] == "34600000001"
        assert db.get_user_by_id("34600000001") is None
        assert db.get_chat_session(session_id)["username"] == "alice@example.com"
        assert db.count_sessions_for_user("alice@example.com") == 1
        assert db.get_latest_chat_session("alice@example.com", "invite-project")["id"] == session_id
        assert db.user_has_project_access("alice@example.com", "invite-project")

    def test_an_admin_can_absorb_a_real_account_too(self, db, auth_service):
        _alice(db)
        db.get_or_create_user("google", "sub-2", "bob@example.com", "Bob", None)
        auth_service.set_whatsapp_phone_number("bob@example.com", "34600000001")

        result = auth_service.set_whatsapp_phone_number("alice@example.com", "34600000001", confirm_merge=True, role="admin")

        assert result["whatsapp_phone_number"] == "34600000001"
        assert db.get_user_by_id("bob@example.com") is None

    def test_a_confirmed_merge_drops_the_absorbed_accounts_project_row_if_the_target_already_has_one(
        self, db, auth_service, project_service, invite_code
    ):
        _alice(db)
        invite = project_service.validate_invite_for_registration(invite_code)
        project_service.redeem_invite(invite, "alice@example.com")
        auth_service.register_via_whatsapp("34600000001", invite_code)

        auth_service.set_whatsapp_phone_number("alice@example.com", "34600000001", confirm_merge=True, role="admin")

        assert db.user_has_project_access("alice@example.com", "invite-project")


class TestPreWiredAdminRegistration:
    """One of the two hardcoded bootstrap admin addresses (Db._ADMIN_EMAILS)
    used to get its User row created straight out of resolve_login(), on
    its very first login — before ever reaching TermsView.vue. That meant
    Terms acceptance itself was silently skipped for them, the one thing
    it must never be, regardless of the invite-code exception these two
    addresses are otherwise entitled to."""

    ADMIN_EMAIL = "enrico.carniani@gmail.com"

    @pytest.fixture
    def admin_identity(self) -> AuthenticatedUser:
        return AuthenticatedUser(
            provider_user_id="sub-admin", email=self.ADMIN_EMAIL, name="Enrico", picture_url=None
        )

    @pytest.fixture
    def admin_auth_service(self, db, admin_identity, project_service) -> AuthService:
        return _auth_service(db, _FakeProvider("admin-credential", admin_identity), project_service)

    def test_logging_in_creates_no_user_row_resolves_to_a_pending_identity_yet_is_invite_exempt_unlike_a_regular_one(
        self, db, admin_auth_service, admin_identity, auth_service, identity
    ):
        """Regression: a pre-wired admin who "Erase all my data"'d their
        User row and then just logs back in (no share link involved)
        must still resolve as invite-exempt — App.vue's own TermsView-
        vs-InviteRequiredView gate (GET /api/auth/pending-status) relies
        on this to avoid dead-ending them at InviteRequiredView, which
        has no path back to a registered account at all."""
        token = admin_auth_service.login("google", "admin-credential")

        assert db.get_user_by_id(admin_identity.email) is None
        verified = admin_auth_service.verify_token(token)
        assert verified is not None
        assert verified.email == admin_identity.email
        assert verified.role is None
        assert admin_auth_service.is_invite_exempt(admin_identity.email) is True
        assert auth_service.is_invite_exempt(identity.email) is False

    def test_completing_registration_needs_no_invite_code_but_still_has_to_happen_to_get_the_admin_role(
        self, db, admin_auth_service, admin_identity
    ):
        """The one exception a pre-wired admin gets over a regular
        identity — but only reachable, same as anyone else, through this
        method, itself only ever called from TermsView.vue's Accept."""
        token = admin_auth_service.login("google", "admin-credential")
        assert admin_auth_service.verify_token(token).role is None

        admin_auth_service.complete_registration(token)

        user = db.get_user_by_id(admin_identity.email)
        assert user is not None
        assert user["role"] == "admin"
        assert admin_auth_service.verify_token(token).role == "admin"


class TestVerifyToken:
    def test_a_login_token_verifies_to_a_pending_identity_then_to_the_registered_one_once_terms_are_accepted(
        self, auth_service, identity, invite_code
    ):
        """No User row exists yet right after login() — verify_token
        still resolves the identity (straight off the token), just with
        role=None, rather than treating it as unauthenticated."""
        token = auth_service.login("google", "good-credential")

        pending = auth_service.verify_token(token)
        assert pending is not None
        assert pending.email == identity.email
        assert pending.provider_user_id == identity.provider_user_id
        assert pending.name == identity.name
        assert pending.role is None

        auth_service.complete_registration(token, invite_code)

        registered = auth_service.verify_token(token)
        assert registered is not None
        assert registered.email == identity.email
        assert registered.role is not None

    def test_a_garbage_forged_or_expired_token_returns_none(self, auth_service, jwt_secret):
        assert auth_service.verify_token("not-a-real-jwt") is None

        forged = jwt.encode({"email": "alice@example.com", "provider": "google"}, "wrong-secret", algorithm="HS256")
        assert auth_service.verify_token(forged) is None

        expired = jwt.encode(
            {"email": "alice@example.com", "provider": "google", "exp": datetime.now(timezone.utc) - timedelta(days=1)},
            jwt_secret, algorithm="HS256",
        )
        assert auth_service.verify_token(expired) is None
