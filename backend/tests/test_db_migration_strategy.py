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
        "INSERT INTO Project (id, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
        "DROP TABLE Invite",
        "DROP TABLE UserProject",
        "CREATE TABLE UserProject ("
        "user_id VARCHAR(255) NOT NULL REFERENCES User(id), "
        "project_id VARCHAR(255) NOT NULL REFERENCES Project(id), "
        "accepted_terms_id INTEGER, "
        "PRIMARY KEY (user_id, project_id))",
        "INSERT INTO UserProject (user_id, project_id) VALUES ('enrico@example.com', 'lluna')",
        "CREATE TABLE legacy_junk (id INTEGER PRIMARY KEY, stuff TEXT)",
    ])

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    columns = {row[1] for row in _query(db_path, "PRAGMA table_info(UserProject)")}
    assert {"invite_id", "invite_timestamp"} <= columns
    tables = {row[0] for row in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "Invite" in tables
    assert "legacy_junk" not in tables
    assert _query(db_path, "SELECT user_id, project_id FROM UserProject") == [("enrico@example.com", "lluna")]
    rows = list(UserProject.select(UserProject.user, UserProject.invite).dicts())
    assert rows == [{"user": "enrico@example.com", "invite": None}]
    assert len(_backups(tmp_path, "test")) == 1


def test_upgrade_renames_the_chatsession_summary_column_and_preserves_its_data(tmp_path):
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}")
    _run_sql(db_path, [
        "INSERT INTO Project (id, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
        "INSERT INTO ChatSession (username, user_id, project_id, type, project_revision, labeled, labeling_revision, channel, ai_summary) "
        "VALUES ('enrico@example.com', 'enrico@example.com', 'lluna', 'live', 1, 0, 0, 'native-chat', 'kept summary')",
        'ALTER TABLE "ChatSession" RENAME COLUMN "ai_summary" TO "summary"',
    ])

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    columns = {row[1] for row in _query(db_path, "PRAGMA table_info(ChatSession)")}
    assert "summary" not in columns
    assert "ai_summary" in columns
    assert _query(db_path, "SELECT ai_summary FROM ChatSession") == [("kept summary",)]


def test_upgrade_survives_the_leftovers_of_an_interrupted_column_rebuild(tmp_path):
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}")
    _run_sql(db_path, [
        "INSERT INTO Project (id, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
        'ALTER TABLE "UserProject" RENAME TO "UserProject__tmp__"',
        "CREATE TABLE UserProject ("
        "user_id VARCHAR(255) NOT NULL REFERENCES User(id), "
        "project_id VARCHAR(255) NOT NULL REFERENCES Project(id), "
        "accepted_terms_id INTEGER, "
        "PRIMARY KEY (user_id, project_id))",
        "INSERT INTO UserProject (user_id, project_id) VALUES ('enrico@example.com', 'lluna')",
    ])
    orphaned = _query(db_path, "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='UserProject__tmp__'")
    assert any("invite_id" in name for (name,) in orphaned)

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    tables = {row[0] for row in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "UserProject__tmp__" not in tables
    columns = {row[1] for row in _query(db_path, "PRAGMA table_info(UserProject)")}
    assert {"invite_id", "invite_timestamp"} <= columns
    assert _query(db_path, "SELECT user_id, project_id FROM UserProject") == [("enrico@example.com", "lluna")]
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
        "INSERT INTO Project (id, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
        "INSERT INTO UserProject (user_id, project_id) VALUES ('enrico@example.com', 'lluna')",
    ])
    _desync_index(db_path, "userproject_invite_id", "UserProject", "user_id")
    assert _query(db_path, "PRAGMA integrity_check") != [("ok",)]

    # REINDEX (below) rebuilds an index's content to match its current
    # on-disk definition, but here the definition itself was rewritten to
    # cover the wrong column — content-only repair can't fix that, so this
    # also needs 'upgrade' to recreate userproject_invite_id for real.
    db = Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    assert _query(db_path, "PRAGMA integrity_check") == [("ok",)]
    assert _query(db_path, "SELECT user_id, project_id FROM UserProject") == [("enrico@example.com", "lluna")]
    assert _query(db_path, "SELECT sql FROM sqlite_master WHERE name = 'userproject_invite_id'") == [
        ('CREATE INDEX "userproject_invite_id" ON "UserProject" ("invite_id")',)
    ]
    # Two backups are taken (repair, then migration) but _timestamped_backup_path
    # is only second-granular, so back-to-back calls within the same second
    # collide onto one file — assert at least one exists rather than an exact count.
    assert len(_backups(tmp_path, "test")) >= 1
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


def test_upgrade_relaxes_a_not_null_constraint_and_preserves_data(tmp_path):
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}")
    _run_sql(db_path, [
        "INSERT INTO Project (id, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
        "INSERT INTO ChatSession (username, user_id, project_id, type, project_revision, labeled, labeling_revision, channel) "
        "VALUES ('enrico@example.com', 'enrico@example.com', 'lluna', 'live', 1, 0, 0, 'native-chat')",
        "PRAGMA legacy_alter_table = ON",
        'ALTER TABLE "User" RENAME TO "User__old__"',
        "PRAGMA legacy_alter_table = OFF",
        "CREATE TABLE User ("
        "id VARCHAR(255) NOT NULL PRIMARY KEY, "
        "provider VARCHAR(255), provider_user_id VARCHAR(255), "
        "email VARCHAR(255) NOT NULL, "
        "name VARCHAR(255), picture_url VARCHAR(255), "
        "created_at DATETIME NOT NULL, last_login DATETIME, "
        "active_project_id VARCHAR(255) REFERENCES Project(id), "
        "role VARCHAR(255) NOT NULL)",
        "INSERT INTO User (id, provider, provider_user_id, email, name, picture_url, created_at, last_login, active_project_id, role) "
        "SELECT id, provider, provider_user_id, email, name, picture_url, created_at, last_login, active_project_id, role FROM User__old__",
        "DROP TABLE User__old__",
    ])

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    notnull = {row[1]: bool(row[3]) for row in _query(db_path, "PRAGMA table_info(User)")}
    assert notnull["email"] is False
    assert "whatsapp_phone_number" in notnull
    assert _query(db_path, "SELECT id, email, role FROM User") == [("enrico@example.com", "enrico@example.com", "user")]
    assert _query(db_path, "SELECT username, project_id, channel FROM ChatSession") == [("enrico@example.com", "lluna", "native-chat")]
    assert _query(db_path, "PRAGMA foreign_key_check") == []


def test_upgrade_relaxes_a_constraint_even_when_every_column_already_matches(tmp_path):
    # Simulates a database an earlier version of this migration code already
    # brought up to date column-wise (e.g. whatsapp_phone_number added) but
    # never revisited email's own NOT NULL — column names alone say nothing
    # has changed, so the schema-differs check must look at constraints too.
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}")
    _run_sql(db_path, [
        "INSERT INTO Project (id, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
        "PRAGMA legacy_alter_table = ON",
        'ALTER TABLE "User" RENAME TO "User__old__"',
        "PRAGMA legacy_alter_table = OFF",
        "CREATE TABLE User ("
        "id VARCHAR(255) NOT NULL PRIMARY KEY, "
        "provider VARCHAR(255), provider_user_id VARCHAR(255), "
        "email VARCHAR(255) NOT NULL, "
        "name VARCHAR(255), picture_url VARCHAR(255), "
        "created_at DATETIME NOT NULL, last_login DATETIME, "
        "active_project_id VARCHAR(255) REFERENCES Project(id), "
        "role VARCHAR(255) NOT NULL, "
        "whatsapp_phone_number VARCHAR(255))",
        "INSERT INTO User (id, provider, provider_user_id, email, name, picture_url, created_at, last_login, active_project_id, role, whatsapp_phone_number) "
        "SELECT id, provider, provider_user_id, email, name, picture_url, created_at, last_login, active_project_id, role, whatsapp_phone_number FROM User__old__",
        "DROP TABLE User__old__",
    ])
    columns = {row[1] for row in _query(db_path, "PRAGMA table_info(User)")}
    assert "whatsapp_phone_number" in columns  # column-name sets already match

    with pytest.raises(ValueError, match="migration-strategy"):
        Db(f"sqlite:///{db_path}")  # 'stop' must still refuse — constraint-only drift is real drift

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    notnull = {row[1]: bool(row[3]) for row in _query(db_path, "PRAGMA table_info(User)")}
    assert notnull["email"] is False
    assert _query(db_path, "SELECT id, email, role FROM User") == [("enrico@example.com", "enrico@example.com", "user")]
    assert _query(db_path, "PRAGMA foreign_key_check") == []


def test_upgrade_rebuild_does_not_collide_with_the_tables_own_pre_existing_indexes(tmp_path):
    # Reproduces a real deployment history: an earlier migration already
    # added whatsapp_phone_number via plain add_column (which leaves every
    # other index on the table untouched), so User keeps its real named
    # indexes right up to the point a later migration needs to rebuild it
    # for email's constraint. Renaming the table carries those indexes
    # along under their original names; recreating the table must not
    # collide with them.
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}")
    _run_sql(db_path, [
        "INSERT INTO Project (id, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
        "PRAGMA legacy_alter_table = ON",
        'ALTER TABLE "User" RENAME TO "User__old__"',
        "PRAGMA legacy_alter_table = OFF",
        "CREATE TABLE User ("
        "id VARCHAR(255) NOT NULL PRIMARY KEY, "
        "provider VARCHAR(255), provider_user_id VARCHAR(255), "
        "email VARCHAR(255) NOT NULL, "
        "name VARCHAR(255), picture_url VARCHAR(255), "
        "created_at DATETIME NOT NULL, last_login DATETIME, "
        "active_project_id VARCHAR(255) REFERENCES Project(id), "
        "role VARCHAR(255) NOT NULL, "
        "whatsapp_phone_number VARCHAR(255))",
        "INSERT INTO User (id, provider, provider_user_id, email, name, picture_url, created_at, last_login, active_project_id, role, whatsapp_phone_number) "
        "SELECT id, provider, provider_user_id, email, name, picture_url, created_at, last_login, active_project_id, role, whatsapp_phone_number FROM User__old__",
        "DROP TABLE User__old__",
        'CREATE UNIQUE INDEX "user_whatsapp_phone_number" ON "User" ("whatsapp_phone_number")',
        'CREATE INDEX "user_active_project_id" ON "User" ("active_project_id")',
        'CREATE UNIQUE INDEX "user_provider_provider_user_id" ON "User" ("provider", "provider_user_id")',
    ])

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    notnull = {row[1]: bool(row[3]) for row in _query(db_path, "PRAGMA table_info(User)")}
    assert notnull["email"] is False
    assert _query(db_path, "SELECT id, email, role FROM User") == [("enrico@example.com", "enrico@example.com", "user")]
    tables = {row[0] for row in _query(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "User__migrating" not in tables
    assert _query(db_path, "PRAGMA foreign_key_check") == []


def test_upgrade_rebuilds_a_table_needing_both_a_constraint_change_and_a_new_not_null_column(tmp_path):
    db_path = tmp_path / "test.db"
    Db(f"sqlite:///{db_path}")
    _run_sql(db_path, [
        "INSERT INTO Project (id, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
        "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
        "PRAGMA legacy_alter_table = ON",
        'ALTER TABLE "ChatSession" RENAME TO "ChatSession__old__"',
        "PRAGMA legacy_alter_table = OFF",
        "CREATE TABLE ChatSession ("
        "id INTEGER NOT NULL PRIMARY KEY, "
        "username VARCHAR(255) NOT NULL, "
        "user_id VARCHAR(255) REFERENCES User(id), "
        "project_id VARCHAR(255) NOT NULL REFERENCES Project(id), "
        "type VARCHAR(255) NOT NULL, "
        "title VARCHAR(255), "
        "project_revision INTEGER NOT NULL, "
        "datetime_start DATETIME, datetime_end DATETIME, "
        "start_state VARCHAR(255), end_state VARCHAR(255), "
        "labeled INTEGER NOT NULL, "
        "comment TEXT, "
        "labeling_revision INTEGER)",
        "INSERT INTO ChatSession (id, username, user_id, project_id, type, project_revision, labeled, labeling_revision) "
        "VALUES (1, 'enrico@example.com', 'enrico@example.com', 'lluna', 'live', 1, 0, 0)",
        "DROP TABLE ChatSession__old__",
        "INSERT INTO Message (role, content, session_id) VALUES ('user', 'hi', 1)",
    ])

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    notnull = {row[1]: bool(row[3]) for row in _query(db_path, "PRAGMA table_info(ChatSession)")}
    assert notnull["labeling_revision"] is True
    assert notnull["channel"] is True
    assert _query(db_path, "SELECT id, username, project_id, labeling_revision, channel FROM ChatSession") == [
        (1, "enrico@example.com", "lluna", 0, "native-chat"),
    ]
    assert _query(db_path, "SELECT session_id, content FROM Message") == [(1, "hi")]
    assert _query(db_path, "PRAGMA foreign_key_check") == []


def test_drop_removes_a_parent_table_referenced_by_a_still_existing_child(tmp_path):
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, FK_PARENT_FIRST_DDL)

    db = Db(f"sqlite:///{db_path}", migration_strategy="drop")

    assert db.get_chat_session(1) is None
    db.save_project_file("user", "proj", "index.yml", b"fresh content", "text/yaml")
    assert db.get_archive("proj", "index.yml") == b"fresh content"


# --- The project_name/project_id merge (SchemaMigrator.migrate_legacy_project_identity) ---

PRE_MERGE_PROJECT_DDL = [
    "CREATE TABLE Project (name TEXT PRIMARY KEY, revision INTEGER, published_revision INTEGER, "
    "draft_edit_count INTEGER, is_paused INTEGER, paused_reason TEXT, manually_paused INTEGER, "
    "project_id TEXT UNIQUE, ui_label TEXT, ui_description TEXT)",
    "CREATE TABLE Archive (id INTEGER PRIMARY KEY, project_name TEXT REFERENCES Project(name), "
    "archive_name TEXT, revision INTEGER, content BLOB, content_type TEXT)",
]


def test_upgrade_merges_a_declared_project_id_onto_the_primary_key(tmp_path):
    """A project that already declared its own project.id (kept in sync
    on Project.project_id pre-merge) gets that value as its new Project.id
    — the old free-text name is gone, and every dependent row follows it."""
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, PRE_MERGE_PROJECT_DDL + [
        "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused, project_id, ui_label) "
        "VALUES ('Lluna Edu Torras', 0, 0, 0, 0, 0, 'lluna', 'Lluna')",
        "INSERT INTO Archive (project_name, archive_name, revision, content, content_type) "
        "VALUES ('Lluna Edu Torras', 'index.yml', 0, 'content', 'text/yaml')",
    ])

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    columns = {row[1] for row in _query(db_path, "PRAGMA table_info(Project)")}
    assert "project_id" not in columns
    assert "id" in columns
    assert _query(db_path, "SELECT id, ui_label FROM Project") == [("lluna", "Lluna")]
    assert _query(db_path, "SELECT project_id, archive_name FROM Archive") == [("lluna", "index.yml")]


def test_upgrade_invents_an_id_for_a_project_that_never_declared_one(tmp_path):
    """No project.id at all pre-merge (the common case) -> a slug of the
    old name/ui_label, per SchemaMigrator._slugify/_unique_legacy_id."""
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, PRE_MERGE_PROJECT_DDL + [
        "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused) "
        "VALUES ('Aprendr català', 0, 0, 0, 0, 0)",
        "INSERT INTO Archive (project_name, archive_name, revision, content, content_type) "
        "VALUES ('Aprendr català', 'index.yml', 0, 'content', 'text/yaml')",
    ])

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    assert _query(db_path, "SELECT id FROM Project") == [("aprendr_catala",)]
    assert _query(db_path, "SELECT project_id FROM Archive") == [("aprendr_catala",)]


def test_upgrade_deduplicates_invented_ids_across_several_id_less_projects(tmp_path):
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, PRE_MERGE_PROJECT_DDL + [
        "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused) "
        "VALUES ('demo', 0, 0, 0, 0, 0)",
        "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused) "
        "VALUES ('Demo', 0, 0, 0, 0, 0)",
    ])

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")

    ids = {row[0] for row in _query(db_path, "SELECT id FROM Project")}
    assert ids == {"demo", "demo_2"}


def test_upgrade_is_idempotent_across_two_boots(tmp_path):
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, PRE_MERGE_PROJECT_DDL + [
        "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused, project_id) "
        "VALUES ('proj', 0, 0, 0, 0, 0, 'proj_id')",
    ])

    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")
    Db(f"sqlite:///{db_path}", migration_strategy="upgrade")  # must not raise or double-migrate

    assert _query(db_path, "SELECT id FROM Project") == [("proj_id",)]


def test_stop_refuses_a_pre_merge_database_untouched(tmp_path):
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, PRE_MERGE_PROJECT_DDL + [
        "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused, project_id) "
        "VALUES ('proj', 0, 0, 0, 0, 0, 'proj_id')",
    ])

    with pytest.raises(ValueError, match="migration-strategy"):
        Db(f"sqlite:///{db_path}")

    assert _query(db_path, "SELECT name, project_id FROM Project") == [("proj", "proj_id")]
    assert _backups(tmp_path, "test") == []


def test_drop_wipes_a_pre_merge_database_same_as_any_other_incompatible_one(tmp_path):
    db_path = tmp_path / "test.db"
    _make_sqlite_file(db_path, PRE_MERGE_PROJECT_DDL + [
        "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused, project_id) "
        "VALUES ('proj', 0, 0, 0, 0, 0, 'proj_id')",
    ])

    db = Db(f"sqlite:///{db_path}", migration_strategy="drop")

    assert db.list_projects() == []
    db.save_project_file("user", "fresh", "index.yml", b"fresh content", "text/yaml")
    assert db.get_archive("fresh", "index.yml") == b"fresh content"
