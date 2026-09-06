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

PROJECT_ID = "proj"


@pytest.fixture
def project(db) -> str:
    db.ensure_project(PROJECT_ID)
    return PROJECT_ID


@pytest.fixture
def project_service(db) -> ProjectService:
    return ProjectService(db)


def _user(db, letter: str) -> str:
    email = f"{letter}@example.com"
    db.get_or_create_user("google", f"sub-{letter}", email, letter.upper(), None)
    return email


def _expired_invite(db, code: str, project: str, max_shares: int = 3):
    db.create_invite(code, project, None, datetime.utcnow() - timedelta(days=1), max_shares=max_shares)


def _maxed_invite(db, code: str, project: str) -> None:
    db.create_invite(code, project, None, datetime.utcnow() + timedelta(days=7), max_shares=1)
    db.record_invite_redemption(_user(db, "a"), project, db.get_invite_by_code(code).id, datetime.utcnow())


class TestCreateInvite:
    def test_generates_distinct_6_character_alphanumeric_codes_recording_their_creator(self, db, project_service, project):
        first = project_service.create_invite(project, created_by=None)
        assert len(first["code"]) == 6
        assert first["code"].isalnum()
        assert first["whatsapp_url"] is None

        assert project_service.create_invite(project, created_by=None)["code"] != first["code"]

        admin = _user(db, "admin")
        created = project_service.create_invite(project, created_by=admin)
        assert db.get_invite_by_code(created["code"]).created_by_id == admin

        with pytest.raises(FileNotFoundError):
            project_service.create_invite("does-not-exist", created_by=None)

    def test_uses_the_configured_valid_days_max_shares_and_whatsapp_number(self, db, project):
        service = ProjectService(db, invite_valid_days=14, invite_max_shares=10, whatsapp_number="15552052260")

        invite = service.create_invite(project, created_by=None)

        assert invite["max_shares"] == 10
        expires_at = datetime.fromisoformat(invite["expires_at"])
        # Within a few seconds of now + 14 days — not asserting an exact
        # instant, since create_invite computes it at call time.
        expected = datetime.utcnow().replace(tzinfo=expires_at.tzinfo) + timedelta(days=14)
        assert abs((expires_at - expected).total_seconds()) < 5
        assert invite["whatsapp_url"] == f"https://wa.me/15552052260?text=Invitation%20code%3A%20{invite['code']}"

    def test_cleans_up_only_expired_invites_that_were_never_redeemed(self, db, project_service, project):
        _expired_invite(db, "OLD001", project)
        _expired_invite(db, "OLD002", project)
        db.record_invite_redemption(_user(db, "a"), project, db.get_invite_by_code("OLD002").id, datetime.utcnow())
        db.create_invite("STILL1", project, None, datetime.utcnow() + timedelta(days=7), max_shares=3)

        project_service.create_invite(project, created_by=None)

        assert db.get_invite_by_code("OLD001") is None
        assert db.get_invite_by_code("OLD002") is not None
        assert db.get_invite_by_code("STILL1") is not None


class TestResolveInviteLink:
    def test_resolves_a_generated_code_granting_a_user_access_the_first_time_and_none_for_an_unknown_code(self, db, project_service, project):
        created = project_service.create_invite(project, created_by=None)
        user = _user(db, "a")

        assert project_service.resolve_invite_link(created["code"], "someone@example.com", "admin") == project
        assert project_service.resolve_invite_link(created["code"], user, "user") == project
        assert db.user_has_project_access(user, project) is True
        assert project_service.resolve_invite_link("NOSUCH", "someone@example.com", "user") is None

    def test_privileged_roles_and_users_with_existing_access_ignore_expiry_and_max_shares(self, db, project_service, project):
        """Only role='user' is ever gated by UserProject (see
        Db.list_projects_with_availability_for_user) — every other role
        keeps the old existence-only lookup, no budget spent."""
        _expired_invite(db, "OLDONE", project, max_shares=1)
        user = _user(db, "a")

        assert project_service.resolve_invite_link("OLDONE", user, "admin") == project
        assert db.user_has_project_access(user, project) is False

        db.record_invite_redemption(user, project, db.get_invite_by_code("OLDONE").id, datetime.utcnow())
        assert project_service.resolve_invite_link("OLDONE", user, "user") == project

    def test_raises_for_a_user_with_no_existing_access_on_an_expired_or_maxed_out_link(self, db, project_service, project):
        _expired_invite(db, "EXPIR1", project)
        _maxed_invite(db, "MAXED1", project)
        newcomer = _user(db, "b")

        with pytest.raises(PermissionError, match="expired"):
            project_service.resolve_invite_link("EXPIR1", newcomer, "user")
        with pytest.raises(PermissionError, match="maximum"):
            project_service.resolve_invite_link("MAXED1", newcomer, "user")


class TestValidateAndRedeemInviteForRegistration:
    def test_refuses_a_missing_unknown_expired_or_maxed_out_code_and_returns_the_row_for_a_valid_one_whose_redemption_counts(self, db, project_service, project):
        _expired_invite(db, "EXPIR1", project)
        _maxed_invite(db, "MAXED1", project)

        for code, match in [(None, "invalid"), ("NOSUCH", "invalid"), ("EXPIR1", "expired"), ("MAXED1", "maximum")]:
            with pytest.raises(PermissionError, match=match):
                project_service.validate_invite_for_registration(code)

        created = project_service.create_invite(project, created_by=None)
        invite = project_service.validate_invite_for_registration(created["code"])
        assert invite.code == created["code"]

        project_service.redeem_invite(invite, _user(db, "c"))
        assert db.count_invite_redemptions(invite.id) == 1
