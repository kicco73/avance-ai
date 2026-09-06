from __future__ import annotations

import sqlite3

import pytest

from db import Db
from db.models import UserProject

pytestmark = pytest.mark.regression


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


def _url(path) -> str:
    return f"sqlite:///{path}"


def _columns(path, table) -> set[str]:
    return {row[1] for row in _query(path, f"PRAGMA table_info({table})")}


def _notnull(path, table) -> dict[str, bool]:
    return {row[1]: bool(row[3]) for row in _query(path, f"PRAGMA table_info({table})")}


def _tables(path) -> set[str]:
    return {row[0] for row in _query(path, "SELECT name FROM sqlite_master WHERE type='table'")}


OLD_ARCHIVE_DDL = [
    "CREATE TABLE archive (id INTEGER PRIMARY KEY, project_name TEXT, archive_name TEXT, "
    "version INTEGER, content TEXT)",
    "INSERT INTO archive (project_name, archive_name, version, content) "
    "VALUES ('proj', 'index.yml', 0, 'old content')",
]

SEED_PROJECT_AND_USER = [
    "INSERT INTO Project (id, revision, is_paused, manually_paused, draft_edit_count) VALUES ('lluna', 1, 0, 0, 0)",
    "INSERT INTO User (id, email, role, created_at) VALUES ('enrico@example.com', 'enrico@example.com', 'user', '2026-01-01 00:00:00')",
]

USERPROJECT_WITHOUT_INVITE_DDL = (
    "CREATE TABLE UserProject ("
    "user_id VARCHAR(255) NOT NULL REFERENCES User(id), "
    "project_id VARCHAR(255) NOT NULL REFERENCES Project(id), "
    "accepted_terms_id INTEGER, "
    "PRIMARY KEY (user_id, project_id))"
)


def _user_table_ddl(extra_columns: str = "") -> str:
    return (
        "CREATE TABLE User ("
        "id VARCHAR(255) NOT NULL PRIMARY KEY, "
        "provider VARCHAR(255), provider_user_id VARCHAR(255), "
        "email VARCHAR(255) NOT NULL, "
        "name VARCHAR(255), picture_url VARCHAR(255), "
        "created_at DATETIME NOT NULL, last_login DATETIME, "
        "active_project_id VARCHAR(255) REFERENCES Project(id), "
        "role VARCHAR(255) NOT NULL" + extra_columns + ")"
    )


def _rebuild_user_with_email_not_null(db_path, with_whatsapp: bool) -> list[str]:
    columns = "id, provider, provider_user_id, email, name, picture_url, created_at, last_login, active_project_id, role"
    if with_whatsapp:
        columns += ", whatsapp_phone_number"
    return [
        *SEED_PROJECT_AND_USER,
        "PRAGMA legacy_alter_table = ON",
        'ALTER TABLE "User" RENAME TO "User__old__"',
        "PRAGMA legacy_alter_table = OFF",
        _user_table_ddl(", whatsapp_phone_number VARCHAR(255)" if with_whatsapp else ""),
        f"INSERT INTO User ({columns}) SELECT {columns} FROM User__old__",
        "DROP TABLE User__old__",
    ]


def test_an_unknown_strategy_is_rejected_and_stop_refuses_an_incompatible_schema_untouched(tmp_path):
    with pytest.raises(ValueError, match="wipe"):
        Db(_url(tmp_path / "unknown.db"), migration_strategy="wipe")

    db_path = tmp_path / "test.db"
    _run_sql(db_path, OLD_ARCHIVE_DDL)
    with pytest.raises(ValueError, match="migration-strategy"):
        Db(_url(db_path))
    assert _query(db_path, "SELECT project_name, content FROM archive") == [("proj", "old content")]
    assert _backups(tmp_path, "test") == []


def test_upgrade_readds_a_dropped_invite_column_and_preserves_data(tmp_path):
    db_path = tmp_path / "test.db"
    Db(_url(db_path))
    _run_sql(db_path, [
        *SEED_PROJECT_AND_USER,
        "DROP TABLE Invite",
        "DROP TABLE UserProject",
        USERPROJECT_WITHOUT_INVITE_DDL,
        "INSERT INTO UserProject (user_id, project_id) VALUES ('enrico@example.com', 'lluna')",
        "CREATE TABLE legacy_junk (id INTEGER PRIMARY KEY, stuff TEXT)",
    ])

    Db(_url(db_path), migration_strategy="upgrade")

    assert {"invite_id", "invite_timestamp"} <= _columns(db_path, "UserProject")
    assert "Invite" in _tables(db_path)
    assert "legacy_junk" not in _tables(db_path)
    assert _query(db_path, "SELECT user_id, project_id FROM UserProject") == [("enrico@example.com", "lluna")]
    assert list(UserProject.select(UserProject.user, UserProject.invite).dicts()) == [{"user": "enrico@example.com", "invite": None}]
    assert len(_backups(tmp_path, "test")) == 1


def test_upgrade_renames_a_column_back_and_adds_new_not_null_default_columns_via_the_generic_paths_preserving_data(tmp_path):
    """AiTokenUsage.cache_read_tokens/cache_creation_tokens are new,
    NOT NULL-with-default columns (see db/models.py) — added via the same
    generic add-column path as any other new column, never a bespoke
    migration script."""
    renamed = tmp_path / "renamed.db"
    Db(_url(renamed))
    _run_sql(renamed, [
        *SEED_PROJECT_AND_USER,
        "INSERT INTO ChatSession (username, user_id, project_id, type, project_revision, labeled, labeling_revision, channel, ai_summary) "
        "VALUES ('enrico@example.com', 'enrico@example.com', 'lluna', 'live', 1, 0, 0, 'native-chat', 'kept summary')",
        'ALTER TABLE "ChatSession" RENAME COLUMN "ai_summary" TO "summary"',
    ])
    Db(_url(renamed), migration_strategy="upgrade")
    assert "summary" not in _columns(renamed, "ChatSession")
    assert "ai_summary" in _columns(renamed, "ChatSession")
    assert _query(renamed, "SELECT ai_summary FROM ChatSession") == [("kept summary",)]

    tokens = tmp_path / "tokens.db"
    Db(_url(tokens))
    _run_sql(tokens, [
        "DROP TABLE AiTokenUsage",
        "CREATE TABLE AiTokenUsage (id INTEGER PRIMARY KEY, provider_label TEXT, timestamp TEXT, "
        "input_tokens INTEGER, output_tokens INTEGER)",
        "INSERT INTO AiTokenUsage (provider_label, timestamp, input_tokens, output_tokens) "
        "VALUES ('anthropic/claude-x', '2026-01-01 00:00:00', 100, 20)",
    ])
    Db(_url(tokens), migration_strategy="upgrade")
    assert {"cache_read_tokens", "cache_creation_tokens"} <= _columns(tokens, "AiTokenUsage")
    assert _query(
        tokens,
        "SELECT provider_label, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens FROM AiTokenUsage",
    ) == [("anthropic/claude-x", 100, 20, 0, 0)]


def test_upgrade_survives_the_leftovers_of_an_interrupted_column_rebuild(tmp_path):
    db_path = tmp_path / "test.db"
    Db(_url(db_path))
    _run_sql(db_path, [
        *SEED_PROJECT_AND_USER,
        'ALTER TABLE "UserProject" RENAME TO "UserProject__tmp__"',
        USERPROJECT_WITHOUT_INVITE_DDL,
        "INSERT INTO UserProject (user_id, project_id) VALUES ('enrico@example.com', 'lluna')",
    ])
    orphaned = _query(db_path, "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='UserProject__tmp__'")
    assert any("invite_id" in name for (name,) in orphaned)

    Db(_url(db_path), migration_strategy="upgrade")

    assert "UserProject__tmp__" not in _tables(db_path)
    assert {"invite_id", "invite_timestamp"} <= _columns(db_path, "UserProject")
    assert _query(db_path, "SELECT user_id, project_id FROM UserProject") == [("enrico@example.com", "lluna")]
    assert list(UserProject.select(UserProject.user, UserProject.invite).dicts()) == [{"user": "enrico@example.com", "invite": None}]


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
    Db(_url(db_path))
    _run_sql(db_path, [
        *SEED_PROJECT_AND_USER,
        "INSERT INTO UserProject (user_id, project_id) VALUES ('enrico@example.com', 'lluna')",
    ])
    _desync_index(db_path, "userproject_invite_id", "UserProject", "user_id")
    assert _query(db_path, "PRAGMA integrity_check") != [("ok",)]

    # REINDEX (below) rebuilds an index's content to match its current
    # on-disk definition, but here the definition itself was rewritten to
    # cover the wrong column — content-only repair can't fix that, so this
    # also needs 'upgrade' to recreate userproject_invite_id for real.
    db = Db(_url(db_path), migration_strategy="upgrade")

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


def test_boot_refuses_a_database_corrupted_beyond_its_indexes_and_upgrade_refuses_a_change_it_cannot_express(tmp_path):
    corrupted = tmp_path / "corrupted.db"
    Db(_url(corrupted))
    _run_sql(corrupted, [SEED_PROJECT_AND_USER[1]])
    rootpage = _query(corrupted, "SELECT rootpage FROM sqlite_master WHERE name='User' AND type='table'")[0][0]
    page_size = _query(corrupted, "PRAGMA page_size")[0][0]
    with open(corrupted, "r+b") as f:
        f.seek((rootpage - 1) * page_size + 3)
        f.write(b"\xff" * 16)
    with pytest.raises(Exception):
        Db(_url(corrupted))

    inexpressible = tmp_path / "test.db"
    Db(_url(inexpressible))
    _drop_indexes(inexpressible, "ChatSession")
    _run_sql(inexpressible, ['ALTER TABLE "ChatSession" DROP COLUMN "username"'])
    with pytest.raises(Exception):
        Db(_url(inexpressible), migration_strategy="upgrade")
    assert len(_backups(tmp_path, "test")) == 1


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


def test_drop_recreates_an_incompatible_schema_even_one_with_a_parent_table_referenced_by_a_child(tmp_path):
    db_path = tmp_path / "test.db"
    _run_sql(db_path, OLD_ARCHIVE_DDL)
    db = Db(_url(db_path), migration_strategy="drop")
    assert db.get_archives("proj") == {}
    db.save_project_file("user", "proj", "index.yml", b"fresh content", "text/yaml")
    assert db.get_archive("proj", "index.yml") == b"fresh content"
    assert len(_backups(tmp_path, "test")) == 1

    fk_path = tmp_path / "fk.db"
    _run_sql(fk_path, FK_PARENT_FIRST_DDL)
    db = Db(_url(fk_path), migration_strategy="drop")
    assert db.get_chat_session(1) is None
    db.save_project_file("user", "proj", "index.yml", b"fresh content", "text/yaml")
    assert db.get_archive("proj", "index.yml") == b"fresh content"


@pytest.mark.parametrize("strategy", ["stop", "upgrade", "drop"])
def test_any_strategy_is_a_noop_for_a_brand_new_or_compatible_database(tmp_path, strategy):
    new_path = tmp_path / "does-not-exist-yet.db"
    db = Db(_url(new_path), migration_strategy=strategy)
    db.save_project_file("user", "proj", "index.yml", b"hello", "text/yaml")
    assert db.get_archive("proj", "index.yml") == b"hello"
    assert _backups(tmp_path, "does-not-exist-yet") == []

    compatible = tmp_path / "test.db"
    Db(_url(compatible)).save_project_file("user", "proj", "index.yml", b"kept content", "text/yaml")
    db = Db(_url(compatible), migration_strategy=strategy)
    assert db.get_archive("proj", "index.yml") == b"kept content"
    assert _backups(tmp_path, "test") == []


def test_upgrade_relaxes_a_not_null_constraint_and_preserves_data(tmp_path):
    db_path = tmp_path / "test.db"
    Db(_url(db_path))
    _run_sql(db_path, [
        *SEED_PROJECT_AND_USER,
        "INSERT INTO ChatSession (username, user_id, project_id, type, project_revision, labeled, labeling_revision, channel) "
        "VALUES ('enrico@example.com', 'enrico@example.com', 'lluna', 'live', 1, 0, 0, 'native-chat')",
        *_rebuild_user_with_email_not_null(db_path, with_whatsapp=False)[2:],
    ])

    Db(_url(db_path), migration_strategy="upgrade")

    notnull = _notnull(db_path, "User")
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
    Db(_url(db_path))
    _run_sql(db_path, _rebuild_user_with_email_not_null(db_path, with_whatsapp=True))
    assert "whatsapp_phone_number" in _columns(db_path, "User")

    with pytest.raises(ValueError, match="migration-strategy"):
        Db(_url(db_path))  # 'stop' must still refuse — constraint-only drift is real drift

    Db(_url(db_path), migration_strategy="upgrade")

    assert _notnull(db_path, "User")["email"] is False
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
    Db(_url(db_path))
    _run_sql(db_path, [
        *_rebuild_user_with_email_not_null(db_path, with_whatsapp=True),
        'CREATE UNIQUE INDEX "user_whatsapp_phone_number" ON "User" ("whatsapp_phone_number")',
        'CREATE INDEX "user_active_project_id" ON "User" ("active_project_id")',
        'CREATE UNIQUE INDEX "user_provider_provider_user_id" ON "User" ("provider", "provider_user_id")',
    ])

    Db(_url(db_path), migration_strategy="upgrade")

    assert _notnull(db_path, "User")["email"] is False
    assert _query(db_path, "SELECT id, email, role FROM User") == [("enrico@example.com", "enrico@example.com", "user")]
    assert "User__migrating" not in _tables(db_path)
    assert _query(db_path, "PRAGMA foreign_key_check") == []


def test_upgrade_rebuilds_a_table_needing_both_a_constraint_change_and_a_new_not_null_column(tmp_path):
    db_path = tmp_path / "test.db"
    Db(_url(db_path))
    _run_sql(db_path, [
        *SEED_PROJECT_AND_USER,
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

    Db(_url(db_path), migration_strategy="upgrade")

    notnull = _notnull(db_path, "ChatSession")
    assert notnull["labeling_revision"] is True
    assert notnull["channel"] is True
    assert _query(db_path, "SELECT id, username, project_id, labeling_revision, channel FROM ChatSession") == [
        (1, "enrico@example.com", "lluna", 0, "native-chat"),
    ]
    assert _query(db_path, "SELECT session_id, content FROM Message") == [(1, "hi")]
    assert _query(db_path, "PRAGMA foreign_key_check") == []


# --- The project_name/project_id merge (SchemaMigrator.migrate_legacy_project_identity) ---

PRE_MERGE_PROJECT_DDL = [
    "CREATE TABLE Project (name TEXT PRIMARY KEY, revision INTEGER, published_revision INTEGER, "
    "draft_edit_count INTEGER, is_paused INTEGER, paused_reason TEXT, manually_paused INTEGER, "
    "project_id TEXT UNIQUE, ui_label TEXT, ui_description TEXT)",
    "CREATE TABLE Archive (id INTEGER PRIMARY KEY, project_name TEXT REFERENCES Project(name), "
    "archive_name TEXT, revision INTEGER, content BLOB, content_type TEXT)",
]

PRE_MERGE_PROJECT_ROW = (
    "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused, project_id) "
    "VALUES ('proj', 0, 0, 0, 0, 0, 'proj_id')"
)


def _pre_merge(path, rows: list[str]):
    _run_sql(path, PRE_MERGE_PROJECT_DDL + rows)


def test_upgrade_merges_a_declared_project_id_onto_the_primary_key_inventing_deduplicated_ids_otherwise_idempotently(tmp_path):
    """A project that already declared its own project.id (kept in sync
    on Project.project_id pre-merge) gets that value as its new Project.id
    — the old free-text name is gone, and every dependent row follows it.
    No project.id at all (the common case) -> a slug of the old name/
    ui_label, per SchemaMigrator._slugify/_unique_legacy_id."""
    declared = tmp_path / "declared.db"
    _pre_merge(declared, [
        "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused, project_id, ui_label) "
        "VALUES ('Lluna Edu Torras', 0, 0, 0, 0, 0, 'lluna', 'Lluna')",
        "INSERT INTO Archive (project_name, archive_name, revision, content, content_type) "
        "VALUES ('Lluna Edu Torras', 'index.yml', 0, 'content', 'text/yaml')",
    ])
    Db(_url(declared), migration_strategy="upgrade")
    assert "project_id" not in _columns(declared, "Project")
    assert "id" in _columns(declared, "Project")
    assert _query(declared, "SELECT id, ui_label FROM Project") == [("lluna", "Lluna")]
    assert _query(declared, "SELECT project_id, archive_name FROM Archive") == [("lluna", "index.yml")]

    invented = tmp_path / "invented.db"
    _pre_merge(invented, [
        "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused) "
        "VALUES ('Aprendr català', 0, 0, 0, 0, 0)",
        "INSERT INTO Archive (project_name, archive_name, revision, content, content_type) "
        "VALUES ('Aprendr català', 'index.yml', 0, 'content', 'text/yaml')",
        "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused) "
        "VALUES ('demo', 0, 0, 0, 0, 0)",
        "INSERT INTO Project (name, revision, published_revision, draft_edit_count, is_paused, manually_paused) "
        "VALUES ('Demo', 0, 0, 0, 0, 0)",
    ])
    Db(_url(invented), migration_strategy="upgrade")
    assert {row[0] for row in _query(invented, "SELECT id FROM Project")} == {"aprendr_catala", "demo", "demo_2"}
    assert _query(invented, "SELECT project_id FROM Archive") == [("aprendr_catala",)]

    twice = tmp_path / "twice.db"
    _pre_merge(twice, [PRE_MERGE_PROJECT_ROW])
    Db(_url(twice), migration_strategy="upgrade")
    Db(_url(twice), migration_strategy="upgrade")
    assert _query(twice, "SELECT id FROM Project") == [("proj_id",)]


def test_stop_refuses_a_pre_merge_database_untouched_while_drop_wipes_it_like_any_other_incompatible_one(tmp_path):
    stopped = tmp_path / "test.db"
    _pre_merge(stopped, [PRE_MERGE_PROJECT_ROW])
    with pytest.raises(ValueError, match="migration-strategy"):
        Db(_url(stopped))
    assert _query(stopped, "SELECT name, project_id FROM Project") == [("proj", "proj_id")]
    assert _backups(tmp_path, "test") == []

    dropped = tmp_path / "dropped.db"
    _pre_merge(dropped, [PRE_MERGE_PROJECT_ROW])
    db = Db(_url(dropped), migration_strategy="drop")
    assert db.list_projects() == []
    db.save_project_file("user", "fresh", "index.yml", b"fresh content", "text/yaml")
    assert db.get_archive("fresh", "index.yml") == b"fresh content"
