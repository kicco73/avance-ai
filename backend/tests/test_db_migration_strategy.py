from __future__ import annotations

import sqlite3

import pytest

from db import Db
from db.models import UserProject

pytestmark = pytest.mark.regression


def _make_sqlite_file(path, ddl_statements):
    conn = sqlite3.connect(path)
    for statement in ddl_statements:
        conn.execute(statement)
    conn.commit()
    conn.close()


def _run_sql(path, statements):
    conn = sqlite3.connect(path)
    for statement in statements:
        conn.execute(statement)
    conn.commit()
    conn.close()


def _query(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _drop_indexes(path, table):
    conn = sqlite3.connect(path)
    names = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)
    )]
    for name in names:
        conn.execute(f'DROP INDEX "{name}"')
    conn.commit()
    conn.close()


def _backups(tmp_path, stem):
    return [f for f in tmp_path.iterdir() if f.name.startswith(f"{stem}-") and f.suffix == ".db"]


OLD_ARCHIVE_DDL = [
    "CREATE TABLE archive (id INTEGER PRIMARY KEY, project_name TEXT, archive_name TEXT, "
    "version INTEGER, content TEXT)",
    "INSERT INTO archive (project_name, archive_name, version, content) "
    "VALUES ('proj', 'index.yml', 0, 'old content')",
]


def test_an_unknown_strategy_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="wipe"):
        Db(f"sqlite:///{tmp_path / 'test.db'}", migration_strategy="wipe")


def test_stop_refuses_to_start_on_an_incompatible_schema(tmp_path):
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, OLD_ARCHIVE_DDL)

    with pytest.raises(ValueError, match="migration-strategy"):
        Db(f"sqlite:///{db_path}")

    assert _query(db_path, "SELECT project_name, content FROM archive") == [("proj", "old content")]
    assert _backups(tmp_path, "test") == []


def test_upgrade_readds_a_dropped_invite_column_and_preserves_data(tmp_path):
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}")
    _run_sql(db_path, [
        "INSERT INTO Project (name, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
        "DROP TABLE Invite",
        "DROP TABLE UserProject",
        "CREATE TABLE UserProject ("
        "user_id VARCHAR(255) NOT NULL REFERENCES User(id), "
        "project_name VARCHAR(255) NOT NULL REFERENCES Project(name), "
        "accepted_terms_id INTEGER, "
        "PRIMARY KEY (user_id, project_name))",
        "INSERT INTO UserProject (user_id, project_name) VALUES ('enrico@example.com', 'lluna')",
        "CREATE TABLE legacy_junk (id INTEGER PRIMARY KEY, stuff TEXT)",
    ])

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    columns = {row[1] for row in _query(db_path, "PRAGMA table_info(UserProject)")}
    assert {"invite_id", "invite_timestamp"} <= columns
    tables = {row[0] for row in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "Invite" in tables
    assert "legacy_junk" not in tables
    assert _query(db_path, "SELECT user_id, project_name FROM UserProject") == [("enrico@example.com", "lluna")]
    rows = list(UserProject.select(UserProject.user, UserProject.invite).dicts())
    assert rows == [{"user": "enrico@example.com", "invite": None}]
    assert len(_backups(tmp_path, "test")) == 1


def test_upgrade_survives_the_leftovers_of_an_interrupted_column_rebuild(tmp_path):
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}")
    _run_sql(db_path, [
        "INSERT INTO Project (name, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
        'ALTER TABLE "UserProject" RENAME TO "UserProject__tmp__"',
        "CREATE TABLE UserProject ("
        "user_id VARCHAR(255) NOT NULL REFERENCES User(id), "
        "project_name VARCHAR(255) NOT NULL REFERENCES Project(name), "
        "accepted_terms_id INTEGER, "
        "PRIMARY KEY (user_id, project_name))",
        "INSERT INTO UserProject (user_id, project_name) VALUES ('enrico@example.com', 'lluna')",
    ])
    orphaned = _query(db_path, "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='UserProject__tmp__'")
    assert any("invite_id" in name for (name,) in orphaned)

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    tables = {row[0] for row in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "UserProject__tmp__" not in tables
    columns = {row[1] for row in _query(db_path, "PRAGMA table_info(UserProject)")}
    assert {"invite_id", "invite_timestamp"} <= columns
    assert _query(db_path, "SELECT user_id, project_name FROM UserProject") == [("enrico@example.com", "lluna")]
    rows = list(UserProject.select(UserProject.user, UserProject.invite).dicts())
    assert rows == [{"user": "enrico@example.com", "invite": None}]


def _desync_index(path, index_name, table, column):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute(
        "UPDATE sqlite_master SET sql=? WHERE name=?",
        (f'CREATE INDEX "{index_name}" ON "{table}" ("{column}")', index_name),
    )
    conn.commit()
    conn.close()


def test_boot_reindexes_a_database_whose_indexes_are_out_of_sync(tmp_path):
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}")
    _run_sql(db_path, [
        "INSERT INTO Project (name, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
        "INSERT INTO UserProject (user_id, project_name) VALUES ('enrico@example.com', 'lluna')",
    ])
    _desync_index(db_path, "userproject_invite_id", "UserProject", "user_id")
    assert _query(db_path, "PRAGMA integrity_check") != [("ok",)]

    db = Db(f"sqlite:///{db_path}")

    assert _query(db_path, "PRAGMA integrity_check") == [("ok",)]
    assert _query(db_path, "SELECT user_id, project_name FROM UserProject") == [("enrico@example.com", "lluna")]
    assert len(_backups(tmp_path, "test")) == 1
    db.erase_user_data("enrico@example.com")
    assert _query(db_path, "SELECT user_id FROM UserProject") == []


def test_boot_refuses_a_database_corrupted_beyond_its_indexes(tmp_path):
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}")
    _run_sql(db_path, [
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
    ])
    rootpage = _query(db_path, "SELECT rootpage FROM sqlite_master WHERE name='User' AND type='table'")[0][0]
    page_size = _query(db_path, "PRAGMA page_size")[0][0]
    with open(db_path, "r+b") as f:
        f.seek((rootpage - 1) * page_size + 3)
        f.write(b"\xff" * 16)

    with pytest.raises(Exception):
        Db(f"sqlite:///{db_path}")


def test_upgrade_refuses_a_change_it_cannot_express(tmp_path):
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}")
    _drop_indexes(db_path, "ChatSession")
    _run_sql(db_path, ['ALTER TABLE "ChatSession" DROP COLUMN "username"'])

    with pytest.raises(Exception):
        Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    assert len(_backups(tmp_path, "test")) == 1


def test_drop_recreates_an_incompatible_schema(tmp_path):
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, OLD_ARCHIVE_DDL)

    db = Db(f"sqlite:///{db_path}", migration_strategy="drop")

    assert db.get_archives("proj") == {}
    db.save_project_file("user", "proj", "index.yml", b"fresh content", "text/yaml")
    assert db.get_archive("proj", "index.yml") == b"fresh content"
    assert len(_backups(tmp_path, "test")) == 1


@pytest.mark.parametrize("strategy", ["stop", "upgrade", "drop"])
def test_any_strategy_is_a_noop_for_a_brand_new_database(tmp_path, strategy):
    db_path = tmp_path / "does-not-exist-yet.db"

    db = Db(f"sqlite:///{db_path}", migration_strategy=strategy)

    db.save_project_file("user", "proj", "index.yml", b"hello", "text/yaml")
    assert db.get_archive("proj", "index.yml") == b"hello"
    assert _backups(tmp_path, "does-not-exist-yet") == []


@pytest.mark.parametrize("strategy", ["stop", "upgrade", "drop"])
def test_a_compatible_schema_and_its_data_are_left_alone(tmp_path, strategy):
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}").save_project_file("user", "proj", "index.yml", b"kept content", "text/yaml")

    db = Db(f"sqlite:///{db_path}", migration_strategy=strategy)

    assert db.get_archive("proj", "index.yml") == b"kept content"
    assert _backups(tmp_path, "test") == []


FK_PARENT_FIRST_DDL = [
    "CREATE TABLE project (name TEXT PRIMARY KEY, revision INTEGER, published_revision INTEGER)",
    "CREATE TABLE chatsession (id INTEGER PRIMARY KEY, username TEXT, "
    "project_name TEXT REFERENCES project(name), datetime_start TEXT, datetime_end TEXT, "
    "start_state TEXT, end_state TEXT)",
    "CREATE TABLE archive (id INTEGER PRIMARY KEY, project_name TEXT REFERENCES project(name), "
    "archive_name TEXT, revision INTEGER, content TEXT)",
    "INSERT INTO project (name, revision, published_revision) VALUES ('proj', 0, 0)",
    "INSERT INTO chatsession (id, username, project_name) VALUES (1, 'user', 'proj')",
]


def test_drop_removes_a_parent_table_referenced_by_a_still_existing_child(tmp_path):
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, FK_PARENT_FIRST_DDL)

    db = Db(f"sqlite:///{db_path}", migration_strategy="drop")

    assert db.get_chat_session(1) is None
    db.save_project_file("user", "proj", "index.yml", b"fresh content", "text/yaml")
    assert db.get_archive("proj", "index.yml") == b"fresh content"
