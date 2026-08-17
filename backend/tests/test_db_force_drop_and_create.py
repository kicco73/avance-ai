"""Tests for database.force-drop-and-create-when-incompatible (see
config.AppConfig/db.Db._drop_and_recreate_if_incompatible) — off by
default: an on-disk schema that doesn't match what this code expects is
left exactly as today's code already leaves it (surfacing as a query
error the first time something touches the mismatched table), unless the
flag is explicitly enabled, in which case every table is dropped and
recreated from scratch at startup instead.
"""
from __future__ import annotations

import sqlite3

import pytest

from db import Db

# Every test in this file verifies a specific behavioral fact about the
# force-drop-and-create flag (on/off, noop cases) rather than a response
# shape — all regression.
pytestmark = pytest.mark.regression


def _make_sqlite_file(path, ddl_statements):
    conn = sqlite3.connect(path)
    for statement in ddl_statements:
        conn.execute(statement)
    conn.commit()
    conn.close()


# An old-shaped 'archive' table (project_name/archive_name/version/content
# — no 'revision' column, no 'history' table at all) — exactly what a
# database created before this version's Archive/History redesign looks
# like on disk.
OLD_ARCHIVE_DDL = [
    "CREATE TABLE archive (id INTEGER PRIMARY KEY, project_name TEXT, archive_name TEXT, "
    "version INTEGER, content TEXT)",
    "INSERT INTO archive (project_name, archive_name, version, content) "
    "VALUES ('proj', 'index.yml', 0, 'old content')",
]


def test_flag_off_leaves_an_incompatible_schema_untouched(tmp_path):
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, OLD_ARCHIVE_DDL)

    db = Db(f"sqlite:///{db_path}")  # force_drop_and_create_when_incompatible defaults to False

    # The old row is still there, byte for byte — nothing was dropped.
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT project_name, archive_name, version, content FROM archive").fetchone()
    finally:
        conn.close()
    assert row == ("proj", "index.yml", 0, "old content")

    # And the mismatch surfaces as a normal query error the moment
    # something actually touches the incompatible table — not at
    # construction time.
    with pytest.raises(Exception):
        db.save_project_files("proj", {"index.yml": "new content"})


def test_flag_on_drops_and_recreates_an_incompatible_schema(tmp_path):
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, OLD_ARCHIVE_DDL)

    db = Db(f"sqlite:///{db_path}", force_drop_and_create_when_incompatible=True)

    # The old (incompatible) row is gone — the table was dropped, not migrated.
    assert db.get_archives("proj") == {}

    # And the new schema works normally from here on.
    db.save_project_file("user", "proj", "index.yml", "fresh content")
    assert db.get_archive("proj", "index.yml") == "fresh content"


def test_flag_on_is_a_noop_for_a_brand_new_database(tmp_path):
    """No pre-existing file at all — nothing that could be incompatible;
    create_tables(safe=True) alone already handles this, same as when the
    flag is off."""
    db_path = tmp_path / "does-not-exist-yet.db"

    db = Db(f"sqlite:///{db_path}", force_drop_and_create_when_incompatible=True)

    db.save_project_file("user", "proj", "index.yml", "hello")
    assert db.get_archive("proj", "index.yml") == "hello"


def test_flag_on_leaves_an_already_compatible_schema_and_its_data_alone(tmp_path):
    db_path = tmp_path / "test.db"
    # First boot: a normal, already-up-to-date database with real data in it.
    Db(f"sqlite:///{db_path}").save_project_file("user", "proj", "index.yml", "kept content")

    # Reopening with the flag on must not touch anything, since the
    # on-disk schema already matches exactly.
    db = Db(f"sqlite:///{db_path}", force_drop_and_create_when_incompatible=True)

    assert db.get_archive("proj", "index.yml") == "kept content"
