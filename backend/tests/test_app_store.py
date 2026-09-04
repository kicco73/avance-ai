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

    def test_search_matches_a_substring_of_the_ui_label(self, db, published_project):
        db.set_project_metadata(published_project, "Customer Support Bot", "Handles tickets")

        assert [app["id"] for app in db.list_projects_for_app_store("user", search="Support")] == [published_project]
        assert db.list_projects_for_app_store("user", search="xyz") == []

    def test_search_matches_a_substring_of_the_ui_description(self, db, published_project):
        db.set_project_metadata(published_project, "Some Bot", "Handles refund requests")

        assert [app["id"] for app in db.list_projects_for_app_store("user", search="refund")] == [published_project]

    def test_search_is_case_insensitive(self, db, published_project):
        db.set_project_metadata(published_project, "Customer Support Bot", None)

        assert [app["id"] for app in db.list_projects_for_app_store("user", search="support")] == [published_project]

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


class TestUninstallAppErasesRecordedData:
    def test_uninstalling_deletes_the_users_sessions_for_that_app(self, db, published_project):
        service = ProjectService(db)
        db.install_project("user", published_project)
        session_id = _create_session(db, published_project, type="live")

        service.uninstall_app("user", published_project)

        assert db.user_has_project_access("user", published_project) is False
        assert db.get_chat_session(session_id) is None

    def test_uninstalling_never_touches_another_users_sessions(self, db, published_project):
        service = ProjectService(db)
        db.get_or_create_user("google", "sub-other", "other", "Other", None)
        db.install_project("user", published_project)
        db.install_project("other", published_project)
        mine = _create_session(db, published_project, username="user", type="live")
        theirs = _create_session(db, published_project, username="other", type="live")

        service.uninstall_app("user", published_project)

        assert db.get_chat_session(mine) is None
        assert db.get_chat_session(theirs) is not None


class TestListSessionSummariesForUserProject:
    def test_orders_summaries_most_recently_closed_first(self, db, published_project):
        service = ProjectService(db)
        older = _create_session(db, published_project, type="live")
        newer = _create_session(db, published_project, type="live")
        db.close_chat_session(older, datetime(2026, 1, 1, 10, 0, 0), "manual-user")
        db.close_chat_session(newer, datetime(2026, 1, 2, 10, 0, 0), "manual-user")
        db.set_session_summary(older, "Older summary")
        db.set_session_summary(newer, "Newer summary")

        summaries = service.get_app_session_summaries("user", published_project)

        assert [s["id"] for s in summaries] == [newer, older]
        assert [s["ai_summary"] for s in summaries] == ["Newer summary", "Older summary"]

    def test_omits_sessions_with_no_summary_yet(self, db, published_project):
        service = ProjectService(db)
        session_id = _create_session(db, published_project, type="live")
        db.close_chat_session(session_id, datetime(2026, 1, 1, 10, 0, 0), "manual-user")

        assert service.get_app_session_summaries("user", published_project) == []


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
