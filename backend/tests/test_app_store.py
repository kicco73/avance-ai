from __future__ import annotations

from datetime import datetime

import pytest

pytestmark = pytest.mark.contract


@pytest.fixture
def published_project(db) -> str:
    db.ensure_project("proj")
    db.publish_project("proj")
    return "proj"


@pytest.fixture
def unpublished_project(db) -> str:
    db.ensure_project("draft-only")
    return "draft-only"


class TestListProjectsForAppStore:
    def test_lists_only_published_projects(self, db, published_project, unpublished_project):
        apps = db.list_projects_for_app_store("user")

        ids = {app["id"] for app in apps}
        assert published_project in ids
        assert unpublished_project not in ids

    def test_installed_flag_reflects_user_project_association(self, db, published_project):
        before = next(app for app in db.list_projects_for_app_store("user") if app["id"] == published_project)
        assert before["installed"] is False

        db.install_project("user", published_project)

        after = next(app for app in db.list_projects_for_app_store("user") if app["id"] == published_project)
        assert after["installed"] is True

    def test_installed_flag_is_per_user(self, db, published_project):
        db.get_or_create_user("google", "sub-other", "other", "Other", None)
        db.install_project("other", published_project)

        mine = next(app for app in db.list_projects_for_app_store("user") if app["id"] == published_project)
        assert mine["installed"] is False


class TestInstallUninstallProject:
    def test_install_then_uninstall_round_trips_access(self, db, published_project):
        assert db.user_has_project_access("user", published_project) is False

        db.install_project("user", published_project)
        assert db.user_has_project_access("user", published_project) is True

        db.uninstall_project("user", published_project)
        assert db.user_has_project_access("user", published_project) is False

    def test_install_is_idempotent(self, db, published_project):
        db.install_project("user", published_project)
        db.install_project("user", published_project)

        assert db.user_has_project_access("user", published_project) is True

    def test_uninstall_of_a_never_installed_project_is_a_no_op(self, db, published_project):
        db.uninstall_project("user", published_project)

        assert db.user_has_project_access("user", published_project) is False


def _create_session(db, project_id, username="user", type="preview"):
    return db.create_chat_session(
        username=username, project_id=project_id, revision=db.get_project_published_revision(project_id),
        datetime_start=datetime(2026, 1, 1, 10, 0, 0), datetime_end=datetime(2026, 1, 1, 10, 0, 0),
        start_state="start", end_state="start", type=type,
    )


class TestDeleteSessionsByUsernameAndType:
    def test_deletes_every_session_of_that_type_across_every_project(self, db, published_project):
        db.ensure_project("other-proj")
        db.publish_project("other-proj")
        first = _create_session(db, published_project)
        second = _create_session(db, "other-proj")
        live = _create_session(db, published_project, type="live")

        db.delete_sessions_by_username_and_type("user", "preview")

        assert db.get_chat_session(first) is None
        assert db.get_chat_session(second) is None
        assert db.get_chat_session(live) is not None

    def test_never_touches_another_users_sessions(self, db, published_project):
        db.get_or_create_user("google", "sub-other", "other", "Other", None)
        mine = _create_session(db, published_project, username="user")
        theirs = _create_session(db, published_project, username="other")

        db.delete_sessions_by_username_and_type("user", "preview")

        assert db.get_chat_session(mine) is None
        assert db.get_chat_session(theirs) is not None

    def test_is_a_no_op_when_there_is_nothing_to_delete(self, db):
        db.delete_sessions_by_username_and_type("user", "preview")


class TestGetFirstImportedSession:
    def test_returns_none_without_any_imported_session(self, db, published_project):
        assert db.get_first_imported_session(published_project) is None

    def test_returns_the_earliest_imported_session(self, db, published_project):
        _create_session(db, published_project, type="live")
        first = _create_session(db, published_project, type="imported")
        second = _create_session(db, published_project, type="imported")

        result = db.get_first_imported_session(published_project)

        assert result is not None
        assert result["id"] == first
        assert result["id"] != second
