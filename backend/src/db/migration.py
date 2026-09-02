from __future__ import annotations

import sqlite3

from playhouse.migrate import SqliteMigrator, migrate


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
