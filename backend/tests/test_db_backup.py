from __future__ import annotations

import os
import stat
import sqlite3
from datetime import datetime

import pytest

from db import Db, database


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


def test_backup_file_path_resolves_to_the_real_file(file_db, tmp_path):
    assert file_db.backup_file_path() == str(tmp_path / "test.db")


def test_export_backup_returns_sqlite_bytes(file_db):
    content = file_db.export_backup()
    assert content.startswith(b"SQLite format 3\x00")


def test_restore_backup_rejects_non_sqlite_content(file_db):
    with pytest.raises(ValueError):
        file_db.restore_backup(b"not a sqlite file")

    # Rejected content must never have touched the working file.
    assert file_db.export_backup().startswith(b"SQLite format 3\x00")


def test_restore_backup_rejects_a_completely_unrelated_schema(file_db, tmp_path):
    wrong = _make_sqlite_bytes(tmp_path, "wrong.db", ["CREATE TABLE unrelated_thing (id INTEGER PRIMARY KEY)"])

    with pytest.raises(ValueError, match="schema"):
        file_db.restore_backup(wrong)

    # Rejected content must never have touched the working file.
    assert file_db.export_backup().startswith(b"SQLite format 3\x00")


def test_restore_backup_rejects_a_missing_column(file_db, tmp_path):
    """Same five tables, but 'message' is missing its session_id column —
    the exact same-tables-wrong-columns case a naive "tables only" check
    would miss."""
    ddl = [
        "CREATE TABLE chatsession (id INTEGER PRIMARY KEY, username TEXT, project_name TEXT, "
        "datetime_start TEXT, datetime_end TEXT, start_state TEXT, end_state TEXT)",
        "CREATE TABLE message (id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp TEXT, audio_text TEXT)",
        "CREATE TABLE settings (user TEXT PRIMARY KEY, project TEXT)",
        "CREATE TABLE signals (id INTEGER PRIMARY KEY, session_id INTEGER, timestamp TEXT, "
        "\"values\" TEXT, old_state TEXT, action TEXT, new_state TEXT, env TEXT)",
        "CREATE TABLE archive (project_name TEXT, archive_name TEXT, revision INTEGER, content BLOB)",
        "CREATE TABLE history (id INTEGER PRIMARY KEY, user_id TEXT, project_name TEXT, "
        "archive_name TEXT, kind TEXT, seq INTEGER, content TEXT)",
    ]
    wrong = _make_sqlite_bytes(tmp_path, "wrong_columns.db", ddl)

    with pytest.raises(ValueError, match="message"):
        file_db.restore_backup(wrong)

    assert file_db.export_backup().startswith(b"SQLite format 3\x00")


def test_restore_backup_accepts_a_schema_matching_backup(file_db):
    """The normal case: a real export from a Db with the identical schema
    must pass the integrity check and actually restore."""
    backup = file_db.export_backup()
    file_db.restore_backup(backup)  # must not raise
    assert file_db.export_backup().startswith(b"SQLite format 3\x00")


def test_restore_backup_rebuilds_the_proxy_target_not_just_reconnects(file_db):
    """The actual fix, not just its effect: restore_backup() must hand
    the shared `database` Proxy a brand new Database object rather than
    closing/reopening the *same* one. Peewee's connection state is
    per-thread — a plain close()+connect() only fixes up the calling
    thread's own state, leaving any other thread that already holds a
    connection (a previous request on a different worker thread, or a
    separate process in a future multi-consumer setup) with a stale
    connection to the file that just got replaced. Rebinding the Proxy to
    a new object sidesteps that: every thread's *next* query lazily opens
    its own fresh connection to it, regardless of how many threads or
    processes are involved."""
    target_before = database.obj

    file_db.restore_backup(file_db.export_backup())

    assert database.obj is not target_before


def test_restore_backup_preserves_the_working_files_permissions(file_db):
    """Regression test: a freshly written temp file gets whatever mode the
    process umask allows, which isn't necessarily the working file's own
    mode. Left unpreserved, a restore could silently leave the file less
    permissive than before (e.g. missing the owner's write bit), and every
    write after that fails with "attempt to write a readonly database"."""
    path = file_db.backup_file_path()
    os.chmod(path, 0o600)  # deliberately not whatever the umask would produce
    backup = file_db.export_backup()

    file_db.restore_backup(backup)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_restore_backup_replaces_data_and_reconnects(file_db):
    kept_id = file_db.create_chat_session(
        username="user",
        project_name="proj",
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
