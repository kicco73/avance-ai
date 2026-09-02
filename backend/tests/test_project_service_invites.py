"""ProjectService's own invite facade (project/invites.py's InviteManager)
— code generation, an already-authenticated identity's own landing
(resolve_invite_link, which only ever gates/grants a role='user' caller),
and the stricter exists/not-expired/under-max-shares gate
AuthService.complete_registration relies on for registration itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from project.project_service import ProjectService

pytestmark = pytest.mark.regression

PROJECT_NAME = "proj"


@pytest.fixture
def project(db) -> str:
    db.ensure_project(PROJECT_NAME)
    return PROJECT_NAME


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db)


class TestCreateInvite:
    def test_generates_a_6_character_alphanumeric_code(self, project_service, project):
        invite = project_service.create_invite(project, created_by=None)

        assert len(invite["code"]) == 6
        assert invite["code"].isalnum()

    def test_uses_the_configured_valid_days_and_max_shares(self, db, project):
        service = ProjectService(db, invite_valid_days=14, invite_max_shares=10)

        invite = service.create_invite(project, created_by=None)

        assert invite["max_shares"] == 10
        expires_at = datetime.fromisoformat(invite["expires_at"])
        # Within a few seconds of now + 14 days — not asserting an exact
        # instant, since create_invite computes it at call time.
        expected = datetime.utcnow().replace(tzinfo=expires_at.tzinfo) + timedelta(days=14)
        assert abs((expires_at - expected).total_seconds()) < 5

    def test_every_call_generates_a_distinct_code(self, project_service, project):
        first = project_service.create_invite(project, created_by=None)
        second = project_service.create_invite(project, created_by=None)

        assert first["code"] != second["code"]

    def test_whatsapp_url_is_none_when_whatsapp_service_is_not_configured(self, project_service, project):
        invite = project_service.create_invite(project, created_by=None)

        assert invite["whatsapp_url"] is None

    def test_whatsapp_url_carries_the_number_and_code_when_configured(self, db, project):
        service = ProjectService(db, whatsapp_number="15552052260")

        invite = service.create_invite(project, created_by=None)

        assert invite["whatsapp_url"] == f"https://wa.me/15552052260?text=Invitation%20code%3A%20{invite['code']}"

    def test_records_who_created_it(self, db, project_service, project):
        db.get_or_create_user("google", "sub-admin", "admin@example.com", "Admin", None)

        invite = project_service.create_invite(project, created_by="admin@example.com")

        row = db.get_invite_by_code(invite["code"])
        assert row.created_by_id == "admin@example.com"

    def test_unknown_project_raises_file_not_found(self, project_service):
        with pytest.raises(FileNotFoundError):
            project_service.create_invite("does-not-exist", created_by=None)


class TestCreateInviteCleansUpExpiredUnredeemedInvites:
    def test_deletes_an_expired_invite_that_was_never_redeemed(self, db, project_service, project):
        expired_at = datetime.utcnow() - timedelta(days=1)
        db.create_invite("OLD001", project, None, expired_at, max_shares=3)

        project_service.create_invite(project, created_by=None)

        assert db.get_invite_by_code("OLD001") is None

    def test_keeps_an_expired_invite_that_was_redeemed_at_least_once(self, db, project_service, project):
        expired_at = datetime.utcnow() - timedelta(days=1)
        db.create_invite("OLD002", project, None, expired_at, max_shares=3)
        db.get_or_create_user("google", "sub-a", "a@example.com", "A", None)
        invite = db.get_invite_by_code("OLD002")
        db.record_invite_redemption("a@example.com", project, invite.id, datetime.utcnow())

        project_service.create_invite(project, created_by=None)

        assert db.get_invite_by_code("OLD002") is not None

    def test_keeps_an_unredeemed_invite_that_has_not_expired_yet(self, db, project_service, project):
        db.create_invite("STILL1", project, None, datetime.utcnow() + timedelta(days=7), max_shares=3)

        project_service.create_invite(project, created_by=None)

        assert db.get_invite_by_code("STILL1") is not None


class TestResolveInviteLink:
    def test_resolves_a_generated_code(self, project_service, project):
        invite = project_service.create_invite(project, created_by=None)

        assert project_service.resolve_invite_link(invite["code"], "someone@example.com", "admin") == project

    def test_an_unknown_code_resolves_to_none(self, project_service):
        assert project_service.resolve_invite_link("NOSUCH", "someone@example.com", "user") is None

    def test_admin_and_supervisor_ignore_expiry_and_max_shares_and_never_get_a_userproject_row(self, db, project_service, project):
        """Only role='user' is ever gated by UserProject (see
        Db.list_projects_with_availability_for_user) — every other role
        keeps the old existence-only lookup, no budget spent."""
        expired_at = datetime.utcnow() - timedelta(days=1)
        db.create_invite("OLDONE", project, None, expired_at, max_shares=1)
        db.get_or_create_user("google", "sub-a", "a@example.com", "A", None)

        assert project_service.resolve_invite_link("OLDONE", "a@example.com", "admin") == project
        assert db.user_has_project_access("a@example.com", project) is False

    def test_a_user_revisiting_a_project_they_already_have_access_to_ignores_expiry_and_max_shares(self, db, project_service, project):
        expired_at = datetime.utcnow() - timedelta(days=1)
        db.create_invite("OLDONE", project, None, expired_at, max_shares=1)
        db.get_or_create_user("google", "sub-a", "a@example.com", "A", None)
        db.record_invite_redemption("a@example.com", project, db.get_invite_by_code("OLDONE").id, datetime.utcnow())

        assert project_service.resolve_invite_link("OLDONE", "a@example.com", "user") == project

    def test_grants_a_user_access_the_first_time_they_reach_a_new_project(self, db, project_service, project):
        created = project_service.create_invite(project, created_by=None)
        db.get_or_create_user("google", "sub-a", "a@example.com", "A", None)

        assert project_service.resolve_invite_link(created["code"], "a@example.com", "user") == project

        assert db.user_has_project_access("a@example.com", project) is True

    def test_raises_for_a_user_with_no_existing_access_when_the_link_is_expired(self, db, project_service, project):
        expired_at = datetime.utcnow() - timedelta(days=1)
        db.create_invite("EXPIR1", project, None, expired_at, max_shares=3)
        db.get_or_create_user("google", "sub-a", "a@example.com", "A", None)

        with pytest.raises(PermissionError, match="expired"):
            project_service.resolve_invite_link("EXPIR1", "a@example.com", "user")

    def test_raises_for_a_user_with_no_existing_access_once_max_shares_is_reached(self, db, project_service, project):
        db.create_invite("MAXED1", project, None, datetime.utcnow() + timedelta(days=7), max_shares=1)
        db.get_or_create_user("google", "sub-a", "a@example.com", "A", None)
        db.get_or_create_user("google", "sub-b", "b@example.com", "B", None)
        invite = db.get_invite_by_code("MAXED1")
        db.record_invite_redemption("a@example.com", project, invite.id, datetime.utcnow())

        with pytest.raises(PermissionError, match="maximum"):
            project_service.resolve_invite_link("MAXED1", "b@example.com", "user")


class TestValidateInviteForRegistration:
    def test_raises_for_a_code_that_does_not_exist(self, project_service):
        with pytest.raises(PermissionError, match="invalid"):
            project_service.validate_invite_for_registration("NOSUCH")

    def test_raises_for_none(self, project_service):
        with pytest.raises(PermissionError, match="invalid"):
            project_service.validate_invite_for_registration(None)

    def test_raises_for_an_expired_code(self, db, project_service, project):
        expired_at = datetime.utcnow() - timedelta(days=1)
        db.create_invite("EXPIR1", project, None, expired_at, max_shares=3)

        with pytest.raises(PermissionError, match="expired"):
            project_service.validate_invite_for_registration("EXPIR1")

    def test_raises_once_max_shares_is_reached(self, db, project_service, project):
        db.create_invite("MAXED1", project, None, datetime.utcnow() + timedelta(days=7), max_shares=1)
        db.get_or_create_user("google", "sub-a", "a@example.com", "A", None)
        invite = db.get_invite_by_code("MAXED1")
        db.record_invite_redemption("a@example.com", project, invite.id, datetime.utcnow())

        with pytest.raises(PermissionError, match="maximum"):
            project_service.validate_invite_for_registration("MAXED1")

    def test_returns_the_invite_row_when_valid(self, project_service, project):
        created = project_service.create_invite(project, created_by=None)

        invite = project_service.validate_invite_for_registration(created["code"])

        assert invite.code == created["code"]


class TestRedeemInvite:
    def test_records_the_redemption_and_it_counts_toward_max_shares(self, db, project_service, project):
        created = project_service.create_invite(project, created_by=None)
        invite = db.get_invite_by_code(created["code"])
        db.get_or_create_user("google", "sub-a", "a@example.com", "A", None)

        project_service.redeem_invite(invite, "a@example.com")

        assert db.count_invite_redemptions(invite.id) == 1
