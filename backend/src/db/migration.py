from __future__ import annotations

import re
import sqlite3
import unicodedata

from playhouse.migrate import SqliteMigrator, migrate


def _slugify(text: str) -> str:
    """Lowercase, ASCII, underscore-separated — 'Aprendr català' -> 'aprendr_catala'."""
    ascii_text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', ascii_text).strip('_').lower()
    if not slug:
        slug = 'project'
    if slug[0].isdigit():
        slug = f'p_{slug}'
    return slug


def _unique_legacy_id(seed: str, used_ids: set[str]) -> str:
    """A fresh id for a project that never declared its own — not already
    in `used_ids` (every id assigned so far, this call included, plus
    every project.id that already existed). No family is assigned (project.
    family is a separate, independent opt-in — see automaton_builder.py):
    a project migrated this way behaves exactly as it always did, isolated
    from automaton.* observation either way."""
    base = _slugify(seed)
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f'{base}_{suffix}'
        suffix += 1
    return candidate


class SchemaMigrator:
    def __init__(self, database, models) -> None:
        self._database = database
        self._models = models

    def expected_schema(self) -> dict[str, set[str]]:
        return {model._meta.table_name: {field.column_name for field in model._meta.sorted_fields} for model in self._models}

    @staticmethod
    def actual_schema(sqlite_path: str) -> dict[str, set[str]]:
        conn = sqlite3.connect(sqlite_path)
        try:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
            return {table: {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")} for table in tables}
        finally:
            conn.close()

    def expected_notnull(self) -> dict[str, dict[str, bool]]:
        return {
            model._meta.table_name: {field.column_name: not field.null for field in model._meta.sorted_fields}
            for model in self._models
        }

    @staticmethod
    def actual_notnull(sqlite_path: str) -> dict[str, dict[str, bool]]:
        conn = sqlite3.connect(sqlite_path)
        try:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
            return {table: {row[1]: bool(row[3]) for row in conn.execute(f"PRAGMA table_info('{table}')")} for table in tables}
        finally:
            conn.close()

    def schema_differs(self, actual: dict[str, set[str]], expected: dict[str, set[str]], path: str) -> bool:
        # Column names matching isn't enough: a prior migration may have
        # already added a table's missing columns without ever revisiting
        # an existing column's own constraint (e.g. NOT NULL -> nullable).
        return actual != expected or bool(self._tables_needing_constraint_rebuild(actual, expected, path))

    def rename_column(self, table: str, old_name: str, new_name: str) -> None:
        """Peewee's own portable rename operation (every backend's migrator
        implements it, same as add_column/drop_column below) — not raw SQL."""
        migrator = SqliteMigrator(self._database)
        migrate(migrator.rename_column(table, old_name, new_name))

    # (table, old column, new column) — every place a project's old `name`
    # string was stored elsewhere, cascaded before Project.name itself is
    # renamed away. User.active_project_id's own column name never
    # changes (already right) — only its stored *value* needs the cascade.
    _PROJECT_NAME_COLUMNS: tuple[tuple[str, str, str], ...] = (
        ('ChatSession', 'project_name', 'project_id'),
        ('Archive', 'project_name', 'project_id'),
        ('Invite', 'project_name', 'project_id'),
        ('UserProject', 'project_name', 'project_id'),
        ('StateRemap', 'project_name', 'project_id'),
        ('Test', 'project_name', 'project_id'),
        ('TestAggregateResult', 'project_name', 'project_id'),
        ('SystemWarning', 'project_name', 'project_id'),
        ('EditHistory', 'project_name', 'project_id'),
        ('ProjectObserverIndex', 'observer_project_name', 'observer_project_id'),
        ('User', 'active_project_id', 'active_project_id'),
    )

    def migrate_legacy_project_identity(self, actual: dict[str, set[str]]) -> None:
        """One-off migration for the project_name/project_id merge:
        Project.name (the old primary key) and Project.project_id (the
        old, optional, YAML-mirroring column) collapse into one mandatory
        Project.id. No-op once already migrated — detected by
        Project.project_id's own absence, so this only ever fires once
        against a database still in the pre-merge shape.

        A project that never declared a project_id gets one invented
        here: a slug of its ui_label or old name (see _slugify/
        _unique_legacy_id above) — every project must have a real id
        going forward. No project.family is assigned either way: family
        is a separate, independent opt-in a project author declares in
        its own index.yml, never inferred from identity."""
        if 'project_id' not in actual.get('Project', set()):
            return
        rows = self._database.execute_sql('SELECT "name", "project_id", "ui_label" FROM "Project"').fetchall()
        used_ids = {row[1] for row in rows if row[1]}
        renames: list[tuple[str, str]] = []
        for old_name, project_id, ui_label in rows:
            new_id = project_id or _unique_legacy_id(ui_label or old_name, used_ids)
            used_ids.add(new_id)
            if new_id != old_name:
                renames.append((old_name, new_id))
        dependent_columns = [
            (table, old_column) for table, old_column, _ in self._PROJECT_NAME_COLUMNS if table in actual
        ]
        self._database.execute_sql('PRAGMA foreign_keys = OFF')
        try:
            with self._database.atomic():
                for old_name, new_id in renames:
                    self._database.execute_sql('UPDATE "Project" SET "name" = ? WHERE "name" = ?', (new_id, old_name))
                    for table, old_column in dependent_columns:
                        self._database.execute_sql(
                            f'UPDATE "{table}" SET "{old_column}" = ? WHERE "{old_column}" = ?', (new_id, old_name),
                        )
                self.rename_column('Project', 'name', 'id')
                # project_id was declared unique=True pre-merge — SQLite
                # refuses a plain ALTER TABLE DROP COLUMN on a column a
                # UNIQUE constraint still covers (inline column-level
                # UNIQUE has no separately-droppable index to remove
                # first), so this needs the same rename-rebuild-copy-drop
                # dance _rebuild_table already does for a NOT NULL change:
                # a fresh Project table in the model's own expected shape
                # naturally has no project_id column to copy into.
                project_model = {m._meta.table_name: m for m in self._models}['Project']
                post_rename_columns = (actual['Project'] - {'name'}) | {'id'}
                self._rebuild_table('Project', project_model, post_rename_columns)
                for table, old_column, new_column in self._PROJECT_NAME_COLUMNS:
                    if table in actual and old_column != new_column:
                        self.rename_column(table, old_column, new_column)
        finally:
            self._database.execute_sql('PRAGMA foreign_keys = ON')

    def migrate(self, actual: dict[str, set[str]], expected: dict[str, set[str]], path: str) -> None:
        migrator = SqliteMigrator(self._database)
        models_by_table = {model._meta.table_name: model for model in self._models}
        new_models = [model for table, model in models_by_table.items() if table not in actual]
        rebuild_tables = self._tables_needing_constraint_rebuild(actual, expected, path)
        self._database.execute_sql('PRAGMA foreign_keys = OFF')
        try:
            for table in sorted(actual.keys() - expected.keys()):
                self._database.execute_sql(f'DROP TABLE IF EXISTS "{table}"')
            operations = []
            for table, columns in expected.items():
                if table not in actual:
                    continue
                model = models_by_table[table]
                if table in rebuild_tables:
                    self._rebuild_table(table, model, actual[table])
                    continue
                for column in sorted(columns - actual[table]):
                    self._database.execute_sql(f'DROP INDEX IF EXISTS "{table}_{column}"')
                    operations.append(migrator.add_column(table, column, model._meta.columns[column]))
                for column in sorted(actual[table] - columns):
                    operations.append(migrator.drop_column(table, column))
            migrate(*operations)
            self._database.create_tables(new_models, safe=True)
        finally:
            self._database.execute_sql('PRAGMA foreign_keys = ON')

    def _tables_needing_constraint_rebuild(
        self, actual: dict[str, set[str]], expected: dict[str, set[str]], path: str,
    ) -> set[str]:
        expected_notnull = self.expected_notnull()
        actual_notnull = self.actual_notnull(path)
        return {
            table for table, columns in expected.items()
            if table in actual and any(
                actual_notnull[table][column] != expected_notnull[table][column]
                for column in columns & actual[table]
            )
        }

    def _rebuild_table(self, table: str, model, actual_columns: set[str]) -> None:
        # FIXME: legacy_alter_table=ON — a plain rename must never let
        # SQLite rewrite another table's REFERENCES "{table}" to the
        # temporary name.
        fields = list(model._meta.sorted_fields)
        shared_columns = sorted(f.column_name for f in fields if f.column_name in actual_columns)
        # New NOT NULL columns get no SQL-level DEFAULT from peewee, so the
        # rebuild's INSERT must supply the field's Python-side default itself.
        new_required_fields = sorted(
            (f for f in fields if f.column_name not in actual_columns and not f.null),
            key=lambda f: f.column_name,
        )
        tmp_name = f"{table}__migrating"
        self._database.execute_sql('PRAGMA legacy_alter_table = ON')
        try:
            self._database.execute_sql(f'ALTER TABLE "{table}" RENAME TO "{tmp_name}"')
        finally:
            self._database.execute_sql('PRAGMA legacy_alter_table = OFF')
        # The rename carries the old table's own named indexes along under
        # their original names (SQLite index names are database-global) —
        # left in place, they'd collide with the fresh table's own indexes
        # of the same name below.
        self._drop_named_indexes(tmp_name)
        self._database.create_tables([model], safe=False)
        insert_columns = shared_columns + [f.column_name for f in new_required_fields]
        select_terms = [f'"{c}"' for c in shared_columns] + ['?'] * len(new_required_fields)
        params = [f.default() if callable(f.default) else f.default for f in new_required_fields]
        insert_columns_sql = ', '.join(f'"{c}"' for c in insert_columns)
        select_sql = ', '.join(select_terms)
        self._database.execute_sql(
            f'INSERT INTO "{table}" ({insert_columns_sql}) SELECT {select_sql} FROM "{tmp_name}"',
            params,
        )
        self._database.execute_sql(f'DROP TABLE "{tmp_name}"')

    def _drop_named_indexes(self, table: str) -> None:
        cursor = self._database.execute_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL",
            (table,),
        )
        for (name,) in cursor.fetchall():
            self._database.execute_sql(f'DROP INDEX "{name}"')
