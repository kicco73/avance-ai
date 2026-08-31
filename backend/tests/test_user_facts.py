"""Tests for tracking.user_facts.UserFacts — the `user` namespace a
trigger/`env:` expression resolves against: every field of the current
session's own User row (db/models.py), except id.
"""
from __future__ import annotations

import pytest

from session import Session
from tracking.user_facts import UserFacts

pytestmark = pytest.mark.contract


@pytest.mark.regression
def test_exposes_every_user_field_except_id(db):
    # The `db` fixture already seeds a User row for _default_session_user's
    # own "user" identity (see conftest.py's own db fixture docstring).
    facts = UserFacts(db).as_dict()

    assert facts == {
        "provider": "test",
        "provider_user_id": "sub-user",
        "email": "user",
        "name": "user",
        "picture_url": None,
        "created_at": facts["created_at"],  # asserted non-None below
        "last_login": None,
        "active_project": None,
        "role": "user",
    }
    assert facts["created_at"] is not None
    assert "id" not in facts


@pytest.mark.regression
def test_reflects_role_and_active_project_changes(db):
    db.set_user_role("user", "admin")
    db.ensure_project("proj")
    db.set_active_project_name("proj", "user")

    facts = UserFacts(db).as_dict()

    assert facts["role"] == "admin"
    assert facts["active_project"] == "proj"


@pytest.mark.regression
def test_is_empty_for_an_identity_with_no_user_row_yet(db):
    with Session().impersonate("nobody@example.com"):
        facts = UserFacts(db).as_dict()

    assert facts == {}


@pytest.mark.regression
def test_reads_session_user_lazily_not_at_construction(db):
    """Matches PersistedEnv/SessionFacts/SystemFacts: constructed once,
    re-reads Session().user on every call — so Session().impersonate(...)
    (wakeup_service.py) scopes an already-built UserFacts correctly too."""
    facts = UserFacts(db)
    assert facts.as_dict()["email"] == "user"

    with Session().impersonate("nobody@example.com"):
        assert facts.as_dict() == {}
