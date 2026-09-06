from __future__ import annotations

from datetime import datetime

import pytest

from project.project_service import ProjectService

pytestmark = pytest.mark.contract


@pytest.fixture
def published_project(db) -> str:
    db.ensure_project("proj")
    db.publish_project("proj")
    return "proj"


def _other_user(db) -> str:
    db.get_or_create_user("google", "sub-other", "other", "Other", None)
    return "other"


def _create_session(db, project_id, username="user", type="preview"):
    return db.create_chat_session(
        username=username, project_id=project_id, revision=db.get_project_published_revision(project_id),
        datetime_start=datetime(2026, 1, 1, 10, 0, 0), datetime_end=datetime(2026, 1, 1, 10, 0, 0),
        start_state="start", end_state="start", type=type,
    )


def _app(db, project_id, username="user", **kwargs) -> dict:
    return next(app for app in db.list_projects_for_app_store(username, **kwargs) if app["id"] == project_id)


class TestListProjectsForAppStore:
    def test_lists_only_published_projects_with_a_per_user_installed_flag(self, db, published_project):
        db.ensure_project("draft-only")

        assert "draft-only" not in {app["id"] for app in db.list_projects_for_app_store("user")}
        assert _app(db, published_project)["installed"] is False

        db.install_project(_other_user(db), published_project)
        assert _app(db, published_project)["installed"] is False

        db.install_project("user", published_project)
        assert _app(db, published_project)["installed"] is True

    def test_search_matches_a_case_insensitive_substring_of_the_ui_label_or_description(self, db, published_project):
        db.set_project_metadata(published_project, "Customer Support Bot", "Handles refund requests")

        for term in ("Support", "support", "refund"):
            assert [app["id"] for app in db.list_projects_for_app_store("user", search=term)] == [published_project]
        assert db.list_projects_for_app_store("user", search="xyz") == []


class TestInstallUninstallProject:
    def test_install_is_idempotent_uninstall_round_trips_access_and_uninstalling_what_was_never_installed_is_a_no_op(self, db, published_project):
        assert db.user_has_project_access("user", published_project) is False
        db.uninstall_project("user", published_project)
        assert db.user_has_project_access("user", published_project) is False

        db.install_project("user", published_project)
        db.install_project("user", published_project)
        assert db.user_has_project_access("user", published_project) is True

        db.uninstall_project("user", published_project)
        assert db.user_has_project_access("user", published_project) is False

    def test_uninstalling_an_app_erases_that_users_own_sessions_for_it_and_nobody_elses(self, db, published_project):
        service = ProjectService(db)
        other = _other_user(db)
        db.install_project("user", published_project)
        db.install_project(other, published_project)
        mine = _create_session(db, published_project, type="live")
        theirs = _create_session(db, published_project, username=other, type="live")

        service.uninstall_app("user", published_project)

        assert db.user_has_project_access("user", published_project) is False
        assert db.get_chat_session(mine) is None
        assert db.get_chat_session(theirs) is not None


def test_session_summaries_list_most_recently_closed_first_omitting_sessions_with_no_summary(db, published_project):
    service = ProjectService(db)
    older = _create_session(db, published_project, type="live")
    newer = _create_session(db, published_project, type="live")
    unsummarized = _create_session(db, published_project, type="live")
    for session_id, closed_at in ((older, datetime(2026, 1, 1, 10)), (newer, datetime(2026, 1, 2, 10)), (unsummarized, datetime(2026, 1, 3, 10))):
        db.close_chat_session(session_id, closed_at, "manual-user")
    db.set_session_summary(older, "Older summary")
    db.set_session_summary(newer, "Newer summary")

    summaries = service.get_app_session_summaries("user", published_project)

    assert [s["id"] for s in summaries] == [newer, older]
    assert [s["ai_summary"] for s in summaries] == ["Newer summary", "Older summary"]


def test_delete_sessions_by_username_and_type_covers_every_project_but_only_that_user_and_type(db, published_project):
    db.ensure_project("other-proj")
    db.publish_project("other-proj")
    other = _other_user(db)
    first = _create_session(db, published_project)
    second = _create_session(db, "other-proj")
    live = _create_session(db, published_project, type="live")
    theirs = _create_session(db, published_project, username=other)

    db.delete_sessions_by_username_and_type("user", "preview")
    db.delete_sessions_by_username_and_type("user", "preview")  # nothing left to delete

    assert db.get_chat_session(first) is None
    assert db.get_chat_session(second) is None
    assert db.get_chat_session(live) is not None
    assert db.get_chat_session(theirs) is not None


def test_get_first_imported_session_returns_the_earliest_one_or_none(db, published_project):
    assert db.get_first_imported_session(published_project) is None

    _create_session(db, published_project, type="live")
    first = _create_session(db, published_project, type="imported")
    second = _create_session(db, published_project, type="imported")

    result = db.get_first_imported_session(published_project)
    assert result is not None
    assert result["id"] == first
    assert result["id"] != second
