from __future__ import annotations

import os
import stat
import sqlite3
from datetime import datetime

import pytest

from db import Db
from db.models import database


def _make_sqlite_bytes(tmp_path, name, ddl_statements):
    path = tmp_path / name
    conn = sqlite3.connect(path)
    for statement in ddl_statements:
        conn.execute(statement)
    conn.commit()
    conn.close()
    return path.read_bytes()


@pytest.fixture
def file_db(tmp_path):
    """Backup/restore act on a real file on disk — the in-memory `db`
    fixture (see conftest.py) has no path for them to operate on."""
    db_path = tmp_path / "test.db"
    return Db(f"sqlite:///{db_path}")


@pytest.mark.contract
def test_backup_file_path_resolves_to_the_real_file(file_db, tmp_path):
    assert file_db.backup_file_path() == str(tmp_path / "test.db")


@pytest.mark.contract
def test_export_backup_returns_sqlite_bytes(file_db):
    content = file_db.export_backup()
    assert content.startswith(b"SQLite format 3\x00")


@pytest.mark.regression
def test_restore_backup_rejects_non_sqlite_content(file_db):
    with pytest.raises(ValueError):
        file_db.restore_backup(b"not a sqlite file")

    # Rejected content must never have touched the working file.
    assert file_db.export_backup().startswith(b"SQLite format 3\x00")


@pytest.mark.regression
def test_restore_backup_rejects_a_completely_unrelated_schema(file_db, tmp_path):
    wrong = _make_sqlite_bytes(tmp_path, "wrong.db", ["CREATE TABLE unrelated_thing (id INTEGER PRIMARY KEY)"])

    with pytest.raises(ValueError, match="schema"):
        file_db.restore_backup(wrong)

    # Rejected content must never have touched the working file.
    assert file_db.export_backup().startswith(b"SQLite format 3\x00")


@pytest.mark.regression
def test_restore_backup_rejects_a_missing_column(file_db, tmp_path):
    """Same five tables, but 'Message' is missing its session_id column —
    the exact same-tables-wrong-columns case a naive "tables only" check
    would miss."""
    ddl = [
        "CREATE TABLE Project (name TEXT PRIMARY KEY, revision INTEGER, published_revision INTEGER, "
        "is_paused INTEGER, paused_reason TEXT, manually_paused INTEGER, project_id TEXT, "
        "ui_label TEXT, ui_description TEXT, draft_edit_count INTEGER)",
        "CREATE TABLE ChatSession (id INTEGER PRIMARY KEY, username TEXT, user_id TEXT, project_name TEXT, "
        "type TEXT, title TEXT, project_revision INTEGER, datetime_start TEXT, datetime_end TEXT, "
        "start_state TEXT, end_state TEXT, labeled INTEGER, comment TEXT, labeling_revision INTEGER)",
        "CREATE TABLE Message (id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp TEXT, audio_text TEXT)",
        "CREATE TABLE User (id TEXT PRIMARY KEY, provider TEXT, provider_user_id TEXT, email TEXT, "
        "name TEXT, picture_url TEXT, created_at TEXT, last_login TEXT, active_project_id TEXT, role TEXT)",
        "CREATE TABLE Tracking (id INTEGER PRIMARY KEY, session_id INTEGER, timestamp TEXT, "
        "\"values\" TEXT, old_state TEXT, action TEXT, new_state TEXT, env TEXT)",
        "CREATE TABLE Archive (project_name TEXT, archive_name TEXT, revision INTEGER, content BLOB)",
        "CREATE TABLE EditHistory (id INTEGER PRIMARY KEY, user_id TEXT, project_name TEXT, "
        "archive_name TEXT, kind TEXT, seq INTEGER, content TEXT)",
        "CREATE TABLE StateRemap (project_name TEXT, old_key TEXT, new_key TEXT)",
        "CREATE TABLE BenchmarkRun (id INTEGER PRIMARY KEY, username TEXT, user_id TEXT, project_name TEXT, "
        "session_id INTEGER, strategy TEXT, project_draft_edit_count INTEGER, session_labeling_revision INTEGER, "
        "batch_segments INTEGER, ai_model_snapshot TEXT, results TEXT)",
        "CREATE TABLE BenchmarkRunObservation (id INTEGER PRIMARY KEY, run_id INTEGER, session_id INTEGER, "
        "message_id INTEGER, timestamp TEXT, \"values\" TEXT, old_state TEXT, action TEXT, new_state TEXT)",
        "CREATE TABLE BenchmarkAggregateResult (id INTEGER PRIMARY KEY, project_name TEXT, revision INTEGER, "
        "project_draft_edit_count INTEGER, kind TEXT, target TEXT, strategy TEXT, results TEXT, created_at TEXT)",
        "CREATE TABLE SessionSummary (id INTEGER PRIMARY KEY, session_id INTEGER, content TEXT)",
        "CREATE TABLE SystemWarning (id INTEGER PRIMARY KEY, user_id TEXT, project_name TEXT, kind TEXT, "
        "message TEXT, timestamp TEXT)",
        "CREATE TABLE ProjectObserverIndex (id INTEGER PRIMARY KEY, project_name TEXT, observer_project_name TEXT)",
        "CREATE TABLE Settings (key TEXT PRIMARY KEY, value TEXT)",
    ]
    wrong = _make_sqlite_bytes(tmp_path, "wrong_columns.db", ddl)

    with pytest.raises(ValueError, match="Message"):
        file_db.restore_backup(wrong)

    assert file_db.export_backup().startswith(b"SQLite format 3\x00")


@pytest.mark.regression
def test_restore_backup_accepts_a_schema_matching_backup(file_db):
    """The normal case: a real export from a Db with the identical schema
    must pass the integrity check and actually restore."""
    backup = file_db.export_backup()
    file_db.restore_backup(backup)  # must not raise
    assert file_db.export_backup().startswith(b"SQLite format 3\x00")


@pytest.mark.regression
def test_restore_backup_rebuilds_the_proxy_target_not_just_reconnects(file_db):
    """restore_backup() must rebind the shared `database` Proxy to a new
    Database object rather than closing/reopening the same one, since
    Peewee's connection state is per-thread."""
    target_before = database.obj

    file_db.restore_backup(file_db.export_backup())

    assert database.obj is not target_before


@pytest.mark.regression
def test_restore_backup_preserves_the_working_files_permissions(file_db):
    """A restore must preserve the working file's permissions rather than
    whatever mode the process umask gives a freshly written temp file."""
    path = file_db.backup_file_path()
    os.chmod(path, 0o600)  # deliberately not whatever the umask would produce
    backup = file_db.export_backup()

    file_db.restore_backup(backup)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.regression
def test_restore_backup_replaces_data_and_reconnects(file_db):
    file_db.ensure_project("proj")
    file_db.publish_project("proj")
    file_db.ensure_project("proj2")
    file_db.publish_project("proj2")
    kept_id = file_db.create_chat_session(
        username="user",
        project_name="proj",
        revision=file_db.get_project_published_revision("proj"),
        datetime_start=datetime(2026, 1, 1),
        datetime_end=datetime(2026, 1, 1),
        start_state="start",
        end_state="start",
    )
    backup = file_db.export_backup()

    # Mutate the working db after the backup snapshot was taken.
    file_db.create_chat_session(
        username="user",
        project_name="proj2",
        revision=file_db.get_project_published_revision("proj2"),
        datetime_start=datetime(2026, 1, 2),
        datetime_end=datetime(2026, 1, 2),
        start_state="start",
        end_state="start",
    )
    assert file_db.get_latest_chat_session("user", "proj2") is not None

    file_db.restore_backup(backup)

    # Back to the pre-mutation snapshot: the kept session is there, the
    # one created afterward is gone.
    assert file_db.get_chat_session(kept_id) is not None
    assert file_db.get_latest_chat_session("user", "proj2") is None
