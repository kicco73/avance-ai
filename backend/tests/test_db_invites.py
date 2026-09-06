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


def _invite(db, code: str, project: str, created_by=None, max_shares: int = 3):
    return db.create_invite(code, project, created_by, datetime.utcnow() + timedelta(days=7), max_shares=max_shares)


def test_a_created_invite_round_trips_by_code_which_is_unique_and_cascades_with_its_project(db, project):
    created = _invite(db, "AB12CD", project, created_by="user")

    fetched = db.get_invite_by_code("AB12CD")
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.project_id == project
    assert fetched.max_shares == 3
    assert fetched.created_by_id == "user"

    assert db.get_invite_by_code("NOSUCH") is None

    with pytest.raises(Exception):  # peewee.IntegrityError — a unique constraint violation
        _invite(db, "AB12CD", project)

    db.delete_archives(project)  # deletes the Project row itself too
    assert db.get_invite_by_code("AB12CD") is None


def test_count_invite_redemptions_counts_each_recorded_redemption_of_that_invite_alone(db, project):
    invite_a = _invite(db, "COUNTA", project)
    invite_b = _invite(db, "COUNTB", project)
    _register(db, "alice@example.com")
    _register(db, "bob@example.com")

    assert db.count_invite_redemptions(invite_a.id) == 0

    db.record_invite_redemption("alice@example.com", project, invite_a.id, datetime.utcnow())
    db.record_invite_redemption("bob@example.com", project, invite_a.id, datetime.utcnow())

    assert db.count_invite_redemptions(invite_a.id) == 2
    assert db.count_invite_redemptions(invite_b.id) == 0


def test_recording_a_redemption_is_idempotent_per_user_moving_them_onto_whichever_invite_came_last(db, project):
    invite_a = _invite(db, "REDEEM2", project)
    invite_b = _invite(db, "REDEEM3", project)
    _register(db, "alice@example.com")

    db.record_invite_redemption("alice@example.com", project, invite_a.id, datetime.utcnow())
    db.record_invite_redemption("alice@example.com", project, invite_b.id, datetime.utcnow())

    assert db.count_invite_redemptions(invite_a.id) == 0
    assert db.count_invite_redemptions(invite_b.id) == 1


def test_a_redemption_never_touches_a_separately_recorded_terms_acceptance_on_the_same_row(db, project):
    invite = _invite(db, "REDEEM4", project)
    _register(db, "alice@example.com")
    db.save_project_files(project, {"legal/terms.md": b"terms"}, {"legal/terms.md": "text/markdown"})
    db.publish_project(project)
    archive_id = db.get_archive_row(project, "legal/terms.md").id

    db.record_invite_redemption("alice@example.com", project, invite.id, datetime.utcnow())
    assert db.get_accepted_terms_archive_id("alice@example.com", project) is None

    db.record_terms_acceptance("alice@example.com", project, archive_id)
    db.record_invite_redemption("alice@example.com", project, invite.id, datetime.utcnow())

    assert db.get_accepted_terms_archive_id("alice@example.com", project) == archive_id
    assert db.count_invite_redemptions(invite.id) == 1
