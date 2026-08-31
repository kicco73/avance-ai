from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.contract


@pytest.fixture
def project(db) -> str:
    db.ensure_project("proj")
    return "proj"


def _register(db, email: str):
    return db.get_or_create_user("google", f"sub-{email}", email, "Some Name", None)


class TestCreateInviteAndGetByCode:
    def test_a_created_invite_round_trips_through_get_invite_by_code(self, db, project):
        expires_at = datetime.utcnow() + timedelta(days=7)
        created = db.create_invite("AB12CD", project, "user", expires_at, max_shares=3)

        fetched = db.get_invite_by_code("AB12CD")

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.project_name_id == project
        assert fetched.max_shares == 3
        assert fetched.created_by_id == "user"

    def test_an_unknown_code_resolves_to_none(self, db):
        assert db.get_invite_by_code("NOSUCH") is None

    def test_the_code_column_is_unique(self, db, project):
        expires_at = datetime.utcnow() + timedelta(days=7)
        db.create_invite("DUPE01", project, None, expires_at, max_shares=3)

        with pytest.raises(Exception):  # peewee.IntegrityError — a unique constraint violation
            db.create_invite("DUPE01", project, None, expires_at, max_shares=3)

    def test_deleting_the_project_cascades_to_its_invites(self, db, project):
        expires_at = datetime.utcnow() + timedelta(days=7)
        db.create_invite("CASC01", project, None, expires_at, max_shares=3)

        db.delete_archives(project)  # deletes the Project row itself too

        assert db.get_invite_by_code("CASC01") is None


class TestCountInviteRedemptions:
    def test_zero_before_anyone_registers_through_it(self, db, project):
        expires_at = datetime.utcnow() + timedelta(days=7)
        invite = db.create_invite("COUNT1", project, None, expires_at, max_shares=3)

        assert db.count_invite_redemptions(invite.id) == 0

    def test_counts_each_recorded_redemption(self, db, project):
        expires_at = datetime.utcnow() + timedelta(days=7)
        invite = db.create_invite("COUNT2", project, None, expires_at, max_shares=3)
        _register(db, "alice@example.com")
        _register(db, "bob@example.com")

        db.record_invite_redemption("alice@example.com", project, invite.id, datetime.utcnow())
        assert db.count_invite_redemptions(invite.id) == 1

        db.record_invite_redemption("bob@example.com", project, invite.id, datetime.utcnow())
        assert db.count_invite_redemptions(invite.id) == 2

    def test_never_counts_a_different_invite(self, db, project):
        expires_at = datetime.utcnow() + timedelta(days=7)
        invite_a = db.create_invite("COUNTA", project, None, expires_at, max_shares=3)
        invite_b = db.create_invite("COUNTB", project, None, expires_at, max_shares=3)
        _register(db, "alice@example.com")

        db.record_invite_redemption("alice@example.com", project, invite_a.id, datetime.utcnow())

        assert db.count_invite_redemptions(invite_a.id) == 1
        assert db.count_invite_redemptions(invite_b.id) == 0


class TestRecordInviteRedemption:
    def test_creates_a_fresh_user_project_row_with_invite_and_timestamp(self, db, project):
        expires_at = datetime.utcnow() + timedelta(days=7)
        invite = db.create_invite("REDEEM1", project, None, expires_at, max_shares=3)
        _register(db, "alice@example.com")
        timestamp = datetime.utcnow()

        db.record_invite_redemption("alice@example.com", project, invite.id, timestamp)

        assert db.get_accepted_terms_archive_id("alice@example.com", project) is None  # unaffected
        assert db.count_invite_redemptions(invite.id) == 1

    def test_is_idempotent_and_overwrites_the_invite_on_a_second_call(self, db, project):
        expires_at = datetime.utcnow() + timedelta(days=7)
        invite_a = db.create_invite("REDEEM2", project, None, expires_at, max_shares=3)
        invite_b = db.create_invite("REDEEM3", project, None, expires_at, max_shares=3)
        _register(db, "alice@example.com")

        db.record_invite_redemption("alice@example.com", project, invite_a.id, datetime.utcnow())
        db.record_invite_redemption("alice@example.com", project, invite_b.id, datetime.utcnow())

        assert db.count_invite_redemptions(invite_a.id) == 0
        assert db.count_invite_redemptions(invite_b.id) == 1

    def test_leaves_a_separately_recorded_terms_acceptance_on_the_same_row_untouched(self, db, project):
        expires_at = datetime.utcnow() + timedelta(days=7)
        invite = db.create_invite("REDEEM4", project, None, expires_at, max_shares=3)
        _register(db, "alice@example.com")
        db.save_project_files(project, {"legal/terms.md": b"terms"}, {"legal/terms.md": "text/markdown"})
        db.publish_project(project)
        archive_id = db.get_archive_row(project, "legal/terms.md").id
        db.record_terms_acceptance("alice@example.com", project, archive_id)

        db.record_invite_redemption("alice@example.com", project, invite.id, datetime.utcnow())

        assert db.get_accepted_terms_archive_id("alice@example.com", project) == archive_id
        assert db.count_invite_redemptions(invite.id) == 1
