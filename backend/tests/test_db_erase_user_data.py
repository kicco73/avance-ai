from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


def _register(db, email: str):
    return db.get_or_create_user("google", f"sub-{email}", email, "Some Name", None)


@pytest.fixture
def project(db) -> str:
    db.ensure_project("proj")
    db.publish_project("proj")
    return "proj"


def test_erase_user_data_removes_the_user_row(db, project):
    _register(db, "alice@example.com")

    db.erase_user_data("alice@example.com")

    assert db.get_user_by_id("alice@example.com") is None


def test_erase_user_data_cascades_through_a_chat_session(db, project):
    _register(db, "alice@example.com")
    revision = db.get_project_published_revision(project)
    session_id = db.create_chat_session("alice@example.com", project, revision, start_state="start")
    message_id = db.save_message("user", "hello", session_id)
    tracking_id = db.save_transition("start", "go", "next", session_id, "INFO")

    db.erase_user_data("alice@example.com")

    assert db.get_chat_session(session_id) is None
    assert db.get_message(message_id) is None
    from db.models import Tracking
    assert Tracking.get_or_none(Tracking.id == tracking_id) is None


def test_erase_user_data_removes_standalone_benchmark_runs_and_system_warnings(db, project):
    _register(db, "alice@example.com")
    run = db.create_benchmark_run(
        "alice@example.com", project, None, "batch",
        project_draft_edit_count=0, session_labeling_revision=None, ai_model_snapshot={},
    )
    db.save_system_warning("alice@example.com", project, "no_session", "boom")

    db.erase_user_data("alice@example.com")

    assert db.find_benchmark_run_by_cache_key(None, "batch", 0, None) is None
    assert db.get_system_warnings("alice@example.com", project) == []
    from db.models import BenchmarkRun
    assert BenchmarkRun.get_or_none(BenchmarkRun.id == run["id"]) is None


def test_erase_user_data_removes_project_edit_history(db, project):
    _register(db, "alice@example.com")
    db.save_project_file("alice@example.com", project, "index.yml", b"v0", "text/yaml")
    db.save_project_file("alice@example.com", project, "index.yml", b"v1", "text/yaml")
    assert db.has_undo("alice@example.com", project, "index.yml")

    db.erase_user_data("alice@example.com")

    assert db.has_undo("alice@example.com", project, "index.yml") is False


def test_erase_user_data_never_touches_another_users_data(db, project):
    _register(db, "alice@example.com")
    _register(db, "bob@example.com")
    revision = db.get_project_published_revision(project)
    alice_session = db.create_chat_session("alice@example.com", project, revision, start_state="start")
    bob_session = db.create_chat_session("bob@example.com", project, revision, start_state="start")

    db.erase_user_data("alice@example.com")

    assert db.get_chat_session(alice_session) is None
    assert db.get_chat_session(bob_session) is not None
    assert db.get_user_by_id("bob@example.com") is not None
