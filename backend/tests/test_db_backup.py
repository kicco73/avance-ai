from __future__ import annotations

import os
import stat
import sqlite3
from datetime import datetime

import pytest

from db import Db


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

    assert file_db.export_backup().startswith(b"SQLite format 3\x00")


@pytest.mark.regression
def test_restore_backup_rejects_a_completely_unrelated_schema(file_db, tmp_path):
    wrong = _make_sqlite_bytes(tmp_path, "wrong.db", ["CREATE TABLE unrelated_thing (id INTEGER PRIMARY KEY)"])

    with pytest.raises(ValueError, match="schema"):
        file_db.restore_backup(wrong)

    assert file_db.export_backup().startswith(b"SQLite format 3\x00")


@pytest.mark.regression
def test_restore_backup_rejects_a_missing_column(file_db, tmp_path):
    """Same five tables, but 'Message' is missing its session_id column —
    the exact same-tables-wrong-columns case a naive "tables only" check
    would miss."""
    ddl = [
        "CREATE TABLE Project (id TEXT PRIMARY KEY, revision INTEGER, published_revision INTEGER, "
        "is_paused INTEGER, paused_reason TEXT, manually_paused INTEGER, "
        "ui_label TEXT, ui_description TEXT, draft_edit_count INTEGER, published_skills TEXT)",
        "CREATE TABLE CoreSession (id INTEGER PRIMARY KEY, username TEXT, user_id TEXT, project_id TEXT, "
        "type TEXT, title TEXT, project_revision INTEGER, datetime_start TEXT, datetime_end TEXT, "
        "start_state TEXT, end_state TEXT, labeled INTEGER, comment TEXT, labeling_revision INTEGER, channel TEXT, "
        "closed_at TEXT, close_reason TEXT, ai_summary TEXT)",
        "CREATE TABLE Message (id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp TEXT, audio_text TEXT)",
        "CREATE TABLE User (id TEXT PRIMARY KEY, provider TEXT, provider_user_id TEXT, email TEXT, "
        "name TEXT, picture_url TEXT, created_at TEXT, last_login TEXT, active_project_id TEXT, role TEXT)",
        "CREATE TABLE Tracking (id INTEGER PRIMARY KEY, session_id INTEGER, timestamp TEXT, "
        "\"values\" TEXT, old_state TEXT, action TEXT, new_state TEXT, env TEXT, origin TEXT)",
        "CREATE TABLE File (hash TEXT PRIMARY KEY, content BLOB, content_type TEXT, size INTEGER)",
        "CREATE TABLE Archive (project_id TEXT, archive_name TEXT, revision INTEGER, hash TEXT)",
        "CREATE TABLE EditHistory (id INTEGER PRIMARY KEY, user_id TEXT, project_id TEXT, "
        "archive_name TEXT, kind TEXT, seq INTEGER, content TEXT)",
        "CREATE TABLE UserProject (user_id TEXT, project_id TEXT, accepted_terms_id INTEGER, "
        "invite_id INTEGER, invite_timestamp TEXT, ai_summary TEXT)",
        "CREATE TABLE Invite (id INTEGER PRIMARY KEY, code TEXT, created_at TEXT, expires_at TEXT, "
        "project_id TEXT, max_shares INTEGER, created_by_id TEXT)",
        "CREATE TABLE StateRemap (project_id TEXT, old_key TEXT, new_key TEXT)",
        "CREATE TABLE Test (id INTEGER PRIMARY KEY, username TEXT, user_id TEXT, project_id TEXT, "
        "session_id INTEGER, strategy TEXT, project_draft_edit_count INTEGER, session_labeling_revision INTEGER, "
        "batch_segments INTEGER, ai_model_snapshot TEXT, results TEXT)",
        "CREATE TABLE TestObservation (id INTEGER PRIMARY KEY, run_id INTEGER, session_id INTEGER, "
        "message_id INTEGER, timestamp TEXT, \"values\" TEXT, old_state TEXT, action TEXT, new_state TEXT)",
        "CREATE TABLE TestAggregateResult (id INTEGER PRIMARY KEY, project_id TEXT, revision INTEGER, "
        "project_draft_edit_count INTEGER, kind TEXT, target TEXT, strategy TEXT, results TEXT, created_at TEXT)",
        "CREATE TABLE SystemWarning (id INTEGER PRIMARY KEY, user_id TEXT, project_id TEXT, kind TEXT, "
        "message TEXT, timestamp TEXT)",
        "CREATE TABLE Settings (key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE Task (id INTEGER PRIMARY KEY, key TEXT, type TEXT, user_id TEXT, project_id TEXT, run_at TEXT, "
        "payload TEXT, ui_label TEXT, ui_description TEXT, status TEXT, error TEXT, created_at TEXT, dispatched_at TEXT, "
        "settled_at TEXT)",
        "CREATE TABLE AiUsage (id INTEGER PRIMARY KEY, provider_label TEXT, timestamp TEXT, "
        "input_tokens INTEGER, output_tokens INTEGER)",
        "CREATE TABLE DbUsage (id INTEGER PRIMARY KEY, query_name TEXT, timestamp TEXT, outcome TEXT, kind TEXT, "
        "count INTEGER, average_duration REAL, median_duration REAL, max_duration REAL)",
        "CREATE TABLE Drive (id INTEGER PRIMARY KEY, project_id TEXT, user_id TEXT, session_id INTEGER, "
        "path TEXT, content BLOB, content_type TEXT, size INTEGER, updated_at TEXT)",
        "CREATE TABLE Translation (id INTEGER PRIMARY KEY, key TEXT, src_lang TEXT, src_text TEXT, "
        "dst_lang TEXT, dst_text TEXT, timestamp TEXT)",
        "CREATE TABLE TrialSession (id INTEGER PRIMARY KEY, user_id TEXT, project_id TEXT, revision INTEGER, "
        "started_at TEXT)",
        "CREATE TABLE AppRating (id INTEGER PRIMARY KEY, user_id TEXT, project_id TEXT, revision INTEGER, "
        "rating INTEGER, timestamp TEXT)",
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
    file_db.restore_backup(backup)
    assert file_db.export_backup().startswith(b"SQLite format 3\x00")


@pytest.mark.regression
def test_restore_backup_preserves_the_working_files_permissions(file_db):
    """A restore must preserve the working file's permissions rather than
    whatever mode the process umask gives a freshly written temp file."""
    path = file_db.backup_file_path()
    os.chmod(path, 0o600)
    backup = file_db.export_backup()

    file_db.restore_backup(backup)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


@pytest.mark.contract
def test_backup_now_writes_a_timestamped_copy_of_the_working_file(file_db):
    path = file_db.backup_now("test reason")
    assert path is not None
    assert path != file_db.backup_file_path()
    assert os.path.exists(path)
    with open(path, "rb") as backup_file:
        assert backup_file.read(len(b"SQLite format 3\x00")) == b"SQLite format 3\x00"


@pytest.mark.contract
def test_backup_now_is_a_noop_for_a_database_with_no_real_file_yet(db):
    """An in-memory (or otherwise not-yet-materialized) database has
    nothing on disk to back up — same os.path.exists guard
    _apply_migration_strategy already uses before touching one."""
    assert db.backup_now("test reason") is None


@pytest.mark.regression
def test_restore_backup_replaces_data_and_reconnects(file_db):
    file_db.ensure_project("proj")
    file_db.publish_project("proj")
    file_db.ensure_project("proj2")
    file_db.publish_project("proj2")
    kept_id = file_db.create_chat_session(
        username="user",
        project_id="proj",
        revision=file_db.get_project_published_revision("proj"),
        datetime_start=datetime(2026, 1, 1),
        datetime_end=datetime(2026, 1, 1),
        start_state="start",
        end_state="start",
    )
    backup = file_db.export_backup()

    file_db.create_chat_session(
        username="user",
        project_id="proj2",
        revision=file_db.get_project_published_revision("proj2"),
        datetime_start=datetime(2026, 1, 2),
        datetime_end=datetime(2026, 1, 2),
        start_state="start",
        end_state="start",
    )
    assert file_db.get_latest_chat_session("user", "proj2") is not None

    file_db.restore_backup(backup)
    assert file_db.get_chat_session(kept_id) is not None
    assert file_db.get_latest_chat_session("user", "proj2") is None


def _free_pages(content: bytes, tmp_path, name: str) -> int:
    path = tmp_path / name
    path.write_bytes(content)
    connection = sqlite3.connect(path)
    try:
        return connection.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        connection.close()


def _on_disk(file_db) -> int:
    path = file_db.backup_file_path()
    return sum(os.path.getsize(p) for p in (path, f"{path}-wal") if os.path.exists(p))


def _fill_and_empty(file_db, rows: int) -> None:
    for index in range(rows):
        file_db.ensure_project(f"proj{index}")
        file_db.save_project_files(
            f"proj{index}", {"index.yml": (b"x" * 40000)}, {"index.yml": "text/yaml"},
        )
    for index in range(rows):
        file_db.delete_archives(f"proj{index}")


@pytest.mark.contract
def test_a_backup_carries_no_free_pages_however_much_the_working_file_deleted(file_db, tmp_path):
    """What an operator downloads is the size of what is in it, not of
    the largest the database ever was — SQLite keeps deleted pages in
    the file and would copy them too."""
    _fill_and_empty(file_db, 20)
    assert _on_disk(file_db) > len(file_db.export_backup())

    assert _free_pages(file_db.export_backup(), tmp_path, "backup.db") == 0


@pytest.mark.contract
def test_reclaiming_gives_the_freed_pages_back_to_the_filesystem(file_db):
    _fill_and_empty(file_db, 20)
    bloated = _on_disk(file_db)

    file_db.reclaim_free_space()

    assert _on_disk(file_db) < bloated
