"""A drive file carries the session that wrote it, and a SQLite trigger
(Db._create_drive_gc_triggers, alongside the File GC ones — see
backend/src/docs/TECHNICAL_DEBT.md) sweeps it the moment a *test*
session's own CoreSession row is deleted, through whichever of the
several paths that delete one — not just the ones the application layer
happens to call explicitly. A live session's deletion never touches the
drive.
"""
from __future__ import annotations

import pytest

from db import Db

pytestmark = pytest.mark.contract

PROJECT = "proj"


def _seed(db: Db) -> int:
    db.ensure_project(PROJECT)
    return db.create_chat_session("user", PROJECT, revision=0, type="test")


def test_a_test_sessions_deletion_sweeps_the_drive_rows_it_wrote(db: Db):
    session_id = _seed(db)
    db.write_drive_file(PROJECT, "user", "reports/last.md", b"ciao", "text/markdown", session_id)

    db.delete_chat_session(session_id)

    assert db.read_drive_file(PROJECT, "user", "reports/last.md") is None


def test_superseding_a_test_session_sweeps_its_drive_rows_too(db: Db):
    """The path an application-level hook would have missed: opening a
    new test session discards the old one through
    Db.delete_sessions_by_username_and_type, never through
    delete_chat_session — the trigger fires regardless, because it is
    attached to the row, not to a caller."""
    session_id = _seed(db)
    db.write_drive_file(PROJECT, "user", "reports/last.md", b"ciao", "text/markdown", session_id)

    db.delete_sessions_by_username_and_type("user", "test")

    assert db.read_drive_file(PROJECT, "user", "reports/last.md") is None


def test_a_live_sessions_deletion_never_touches_the_drive(db: Db):
    db.ensure_project(PROJECT)
    session_id = db.create_chat_session("user", PROJECT, revision=0, type="live")
    db.write_drive_file(PROJECT, "user", "reports/last.md", b"ciao", "text/markdown", session_id)

    db.delete_chat_session(session_id)

    assert db.read_drive_file(PROJECT, "user", "reports/last.md")[0] == b"ciao"


def test_a_file_written_with_no_firing_session_is_unaffected_by_any_session_deletion(db: Db):
    session_id = _seed(db)
    db.write_drive_file(PROJECT, "user", "reports/last.md", b"ciao", "text/markdown", None)

    db.delete_chat_session(session_id)

    assert db.read_drive_file(PROJECT, "user", "reports/last.md")[0] == b"ciao"


def test_delete_drive_files_for_sessions_of_type_clears_the_drive_but_keeps_the_session_row(db: Db):
    session_id = _seed(db)
    db.write_drive_file(PROJECT, "user", "reports/last.md", b"ciao", "text/markdown", session_id)

    cleared = db.delete_drive_files_for_sessions_of_type(PROJECT, "user", "test")

    assert cleared == 1
    assert db.read_drive_file(PROJECT, "user", "reports/last.md") is None
    assert db.get_chat_session(session_id) is not None


def test_delete_drive_files_for_sessions_of_type_leaves_other_users_projects_and_types_alone(db: Db):
    session_id = _seed(db)
    db.write_drive_file(PROJECT, "user", "reports/mine.md", b"mio", "text/markdown", session_id)

    other_project = "other"
    db.ensure_project(other_project)
    other_project_session = db.create_chat_session("user", other_project, revision=0, type="test")
    db.write_drive_file(other_project, "user", "reports/mine.md", b"altro-progetto", "text/markdown", other_project_session)

    db.get_or_create_user("test", "sub-other", "other-user", "Other", None)
    other_user_session = db.create_chat_session("other-user", PROJECT, revision=0, type="test")
    db.write_drive_file(PROJECT, "other-user", "reports/mine.md", b"altro-utente", "text/markdown", other_user_session)

    live_session = db.create_chat_session("user", PROJECT, revision=0, type="live")
    db.write_drive_file(PROJECT, "user", "reports/live.md", b"live", "text/markdown", live_session)

    db.delete_drive_files_for_sessions_of_type(PROJECT, "user", "test")

    assert db.read_drive_file(other_project, "user", "reports/mine.md")[0] == b"altro-progetto"
    assert db.read_drive_file(PROJECT, "other-user", "reports/mine.md")[0] == b"altro-utente"
    assert db.read_drive_file(PROJECT, "user", "reports/live.md")[0] == b"live"
