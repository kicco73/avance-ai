from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone

from .benchmark_runs import BenchmarkRunMixin
from .history import HistoryMixin
from .jobs import JobMixin
from .messages import MessageMixin
from .observability import ObservabilityMixin
from .projects import ProjectMixin
from .session_summaries import SessionSummaryMixin
from .sessions import SessionMixin
from .settings import SettingsMixin
from .tracking import TrackingMixin

from playhouse.db_url import connect, parse as parse_db_url

from .models import (
    Archive, BenchmarkRun, BenchmarkRunObservation, ChatSession, History, Job, Message, Project,
    ProjectObserverIndex, Settings, SessionSummary, StateRemap, SystemWarning, Tracking, database,
)

logger = logging.getLogger(__name__)

def _utc_iso(dt: datetime | None) -> str | None:
    """Every DateTimeField in this module is written with
    `default=datetime.utcnow` — a naive datetime that's really always UTC,
    never the server's local time. `dt.isoformat()` alone would drop that
    fact on the floor, and a frontend `new Date(...)` parses a
    timezone-less ISO string as *local* time, not UTC — silently shifting
    every timestamp by the browser's own UTC offset. Stamping the
    timezone explicitly here is what lets the frontend be the only place
    that ever converts to the user's local time for display (see
    MessageBubble.vue's formatTimestamp). `dt` is None for an imported
    session/message with no real timestamp (see ChatSession.source) —
    stays None rather than raising, since that's a legitimate value here,
    not a bug."""
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt is not None else None

class Db(
    SessionMixin,
    MessageMixin,
    TrackingMixin,
    SettingsMixin,
    ProjectMixin,
    HistoryMixin,
    JobMixin,
    BenchmarkRunMixin,
    SessionSummaryMixin,
    ObservabilityMixin):

    _SQLITE_MAGIC = b"SQLite format 3\x00"
    _MODELS = (
        Project, ChatSession, Message, Settings, Tracking, Archive, History, StateRemap, Job,
        BenchmarkRun, BenchmarkRunObservation, SessionSummary, SystemWarning, ProjectObserverIndex,
    )

    def __init__(self, database_url: str, force_drop_and_create_when_incompatible: bool=False) -> None:
        self._database_url = database_url
        database.initialize(connect(database_url))
        database.connect(reuse_if_open=True)
        self._enable_foreign_keys()
        if force_drop_and_create_when_incompatible:
            self._drop_and_recreate_if_incompatible()
        database.create_tables(self._MODELS, safe=True)
        self._backfill_projects()

    @staticmethod
    def _backfill_projects() -> None:
        """Project didn't exist before ChatSession.project_name/
        Archive.project_name became foreign keys to it — an install with
        data from before this migration has distinct project_name values
        in those tables with no corresponding Project row yet. Idempotent
        (get_or_create), so safe to run on every startup rather than only
        once."""
        names = {row.project_name_id for row in ChatSession.select(ChatSession.project_name).distinct()}
        names |= {row.project_name_id for row in Archive.select(Archive.project_name).distinct()}
        for name in names:
            Project.get_or_create(name=name, defaults={'revision': 0, 'published_revision': None})

    def _drop_and_recreate_if_incompatible(self) -> None:
        path = self.backup_file_path()
        if not os.path.exists(path):
            return
        actual = self._actual_schema(path)
        if not actual:
            return
        if actual == self._expected_schema():
            return
        logger.warning("Database schema at '%s' doesn't match what this code expects — dropping and recreating every table from scratch (database.force-drop-and-create-when-incompatible is enabled).", path)
        # Dropping a parent table (e.g. 'project') while a child table
        # (e.g. 'chatsession'/'archive') still has a FOREIGN KEY
        # referencing it raises IntegrityError under SQLite's own FK
        # enforcement — verified DROP TABLE is checked, not just DML.
        # `actual`'s own table order reflects sqlite_master's on-disk
        # order, not a dependency-safe one, so foreign key checking is
        # switched off for the duration of the drop only, then restored
        # before anything gets recreated.
        database.execute_sql('PRAGMA foreign_keys = OFF')
        try:
            for table in actual:
                database.execute_sql(f'DROP TABLE IF EXISTS "{table}"')
        finally:
            self._enable_foreign_keys()

    @staticmethod
    def _enable_foreign_keys() -> None:
        database.execute_sql('PRAGMA foreign_keys = ON')

    def backup_file_path(self) -> str:
        return os.path.abspath(parse_db_url(self._database_url)['database'])

    def export_backup(self) -> bytes:
        with open(self.backup_file_path(), 'rb') as f:
            return f.read()

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
        database.initialize(connect(self._database_url))
        database.connect(reuse_if_open=True)
        self._enable_foreign_keys()

