from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

from logging_factory import LoggerFactory

from .test_aggregates import TestAggregateMixin
from .tests import TestMixin
from .history import HistoryMixin
from .invites import InviteMixin
from .messages import MessageMixin
from .observability import ObservabilityMixin
from .projects import ProjectMixin
from .session_summaries import SessionSummaryMixin
from .sessions import SessionMixin
from .settings import SettingsMixin
from .users import UserMixin
from .user_projects import UserProjectMixin
from .tracking import TrackingMixin

from playhouse.db_url import connect, parse as parse_db_url
from playhouse.migrate import SqliteMigrator, migrate

from .models import (
    Archive, ChatSession, EditHistory, Invite, Message,
    Project, ProjectObserverIndex, Settings, User, SessionSummary, StateRemap, SystemWarning, Test,
    TestAggregateResult, TestObservation, Tracking, UserProject,
    database,
)

logger = LoggerFactory.get_logger(__name__)

def _utc_iso(dt: datetime | None) -> str | None:
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt is not None else None

class Db(
    SessionMixin,
    MessageMixin,
    TrackingMixin,
    UserMixin,
    ProjectMixin,
    HistoryMixin,
    TestMixin,
    TestAggregateMixin,
    SessionSummaryMixin,
    SettingsMixin,
    UserProjectMixin,
    InviteMixin,
    ObservabilityMixin):

    _SQLITE_MAGIC = b"SQLite format 3\x00"
    _MODELS = (
        Project, ChatSession, Message, User, Tracking, Archive, EditHistory, StateRemap,
        Test, TestObservation, TestAggregateResult, SessionSummary, SystemWarning,
        ProjectObserverIndex, Settings, UserProject, Invite,
    )

    MIGRATION_STRATEGIES = ('stop', 'upgrade', 'drop')

    def __init__(self, database_url: str, migration_strategy: str = 'stop') -> None:
        if migration_strategy not in self.MIGRATION_STRATEGIES:
            raise ValueError(f"Unknown migration strategy '{migration_strategy}' — expected one of {self.MIGRATION_STRATEGIES}.")
        self._database_url = database_url
        database.initialize(connect(database_url, pragmas={'foreign_keys': 1}))
        database.connect(reuse_if_open=True)
        self._repair_indexes_if_inconsistent()
        self._apply_migration_strategy(migration_strategy)
        database.create_tables(self._MODELS, safe=True)
        self._backfill_projects()

    def _repair_indexes_if_inconsistent(self) -> None:
        problems = [row[0] for row in database.execute_sql('PRAGMA integrity_check').fetchall()]
        if problems == ['ok']:
            return
        path = self.backup_file_path()
        if not all('index' in problem for problem in problems):
            raise ValueError(f"Database at '{path}' is corrupted beyond its indexes — refusing to touch it (integrity_check: {problems}).")
        backup_path = self._timestamped_backup_path(path)
        self._backup_to_path(backup_path)
        logger.warning("Database at '%s' has indexes inconsistent with their tables (integrity_check: %s) — backed it up to '%s', now rebuilding every index from table data (REINDEX).", path, problems, backup_path)
        database.execute_sql('REINDEX')
        problems = [row[0] for row in database.execute_sql('PRAGMA integrity_check').fetchall()]
        if problems != ['ok']:
            raise ValueError(f"Database at '{path}' is still corrupted after rebuilding its indexes — refusing to touch it (integrity_check: {problems}; the pre-repair backup is at '{backup_path}').")

    @staticmethod
    def _backfill_projects() -> None:
        names = {row.project_name_id for row in ChatSession.select(ChatSession.project_name).distinct()}
        names |= {row.project_name_id for row in Archive.select(Archive.project_name).distinct()}
        for name in names:
            Project.get_or_create(name=name, defaults={'revision': 0, 'published_revision': None})

    def _apply_migration_strategy(self, strategy: str) -> None:
        path = self.backup_file_path()
        if not os.path.exists(path):
            return
        actual = self._actual_schema(path)
        if not actual:
            return
        expected = self._expected_schema()
        if actual == expected:
            return
        if strategy == 'stop':
            raise ValueError(f"Database schema at '{path}' doesn't match what this code expects and database.migration-strategy is 'stop' — set it to 'upgrade' or 'drop', or fix the database by hand.")
        backup_path = self._timestamped_backup_path(path)
        self._backup_to_path(backup_path)
        if strategy == 'upgrade':
            logger.warning("Database schema at '%s' doesn't match what this code expects — backed it up to '%s', now migrating it in place (database.migration-strategy is 'upgrade').", path, backup_path)
            self._migrate_schema(actual, expected)
            if self._actual_schema(path) != expected:
                raise ValueError(f"Database schema at '{path}' still doesn't match after in-place migration — refusing to touch the data any further (the pre-migration backup is at '{backup_path}').")
            return
        logger.warning("Database schema at '%s' doesn't match what this code expects — backed it up to '%s', now dropping and recreating every table from scratch (database.migration-strategy is 'drop').", path, backup_path)
        database.execute_sql('PRAGMA foreign_keys = OFF')
        try:
            for table in actual:
                database.execute_sql(f'DROP TABLE IF EXISTS "{table}"')
        finally:
            self._enable_foreign_keys()

    def _migrate_schema(self, actual: dict[str, set[str]], expected: dict[str, set[str]]) -> None:
        migrator = SqliteMigrator(database)
        models_by_table = {model._meta.table_name: model for model in self._MODELS}
        new_models = [model for table, model in models_by_table.items() if table not in actual]
        database.execute_sql('PRAGMA foreign_keys = OFF')
        try:
            for table in sorted(actual.keys() - expected.keys()):
                database.execute_sql(f'DROP TABLE IF EXISTS "{table}"')
            operations = []
            for table, columns in expected.items():
                if table not in actual:
                    continue
                model = models_by_table[table]
                for column in sorted(columns - actual[table]):
                    database.execute_sql(f'DROP INDEX IF EXISTS "{table}_{column}"')
                    operations.append(migrator.add_column(table, column, model._meta.columns[column]))
                for column in sorted(actual[table] - columns):
                    operations.append(migrator.drop_column(table, column))
            migrate(*operations)
            database.create_tables(new_models, safe=True)
        finally:
            self._enable_foreign_keys()

    @staticmethod
    def _enable_foreign_keys() -> None:
        database.execute_sql('PRAGMA foreign_keys = ON')

    def backup_file_path(self) -> str:
        return os.path.abspath(parse_db_url(self._database_url)['database'])

    @staticmethod
    def _timestamped_backup_path(path: str) -> str:
        root, ext = os.path.splitext(path)
        return f'{root}-{datetime.now().strftime("%Y-%m-%d_%H:%M:%S")}{ext}'

    @staticmethod
    def _backup_to_path(dest_path: str) -> None:
        dest_conn = sqlite3.connect(dest_path)
        try:
            database.connection().backup(dest_conn)
        finally:
            dest_conn.close()

    def export_backup(self) -> bytes:
        fd, tmp_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        try:
            self._backup_to_path(tmp_path)
            with open(tmp_path, 'rb') as f:
                return f.read()
        finally:
            os.remove(tmp_path)

    @classmethod
    def _expected_schema(cls) -> dict[str, set[str]]:
        return {model._meta.table_name: {field.column_name for field in model._meta.sorted_fields} for model in cls._MODELS}

    @staticmethod
    def _actual_schema(sqlite_path: str) -> dict[str, set[str]]:
        conn = sqlite3.connect(sqlite_path)
        try:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
            return {table: {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")} for table in tables}
        finally:
            conn.close()

    def _check_schema(self, sqlite_path: str) -> None:
        expected = self._expected_schema()
        actual = self._actual_schema(sqlite_path)
        missing_tables = expected.keys() - actual.keys()
        extra_tables = actual.keys() - expected.keys()
        if missing_tables or extra_tables:
            raise ValueError("Backup schema doesn't match: " + (f'missing table(s) {sorted(missing_tables)} ' if missing_tables else '') + (f'unexpected table(s) {sorted(extra_tables)}' if extra_tables else ''))
        for table, columns in expected.items():
            missing_columns = columns - actual[table]
            extra_columns = actual[table] - columns
            if missing_columns or extra_columns:
                raise ValueError(f"Backup schema doesn't match: table '{table}' " + (f'missing column(s) {sorted(missing_columns)} ' if missing_columns else '') + (f'has unexpected column(s) {sorted(extra_columns)}' if extra_columns else ''))

    def restore_backup(self, content: bytes) -> None:
        if not content.startswith(self._SQLITE_MAGIC):
            raise ValueError('Uploaded file is not a valid SQLite database.')
        path = self.backup_file_path()
        tmp_path = f'{path}.restoring'
        with open(tmp_path, 'wb') as f:
            f.write(content)
        try:
            self._check_schema(tmp_path)
        except Exception:
            os.remove(tmp_path)
            raise
        try:
            os.chmod(tmp_path, os.stat(path).st_mode)
        except OSError:
            pass
        database.close()
        os.replace(tmp_path, path)
        database.initialize(connect(self._database_url, pragmas={'foreign_keys': 1}))
        database.connect(reuse_if_open=True)

