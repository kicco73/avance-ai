from __future__ import annotations

from datetime import datetime

import pytest

from db import Db


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
