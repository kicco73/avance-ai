from __future__ import annotations

import json
import uuid

from db import Db
from jobs import Job
from logging_factory import LoggerFactory
from tracking.session_import import SessionImportManager

logger = LoggerFactory.get_logger(__name__)


class ProjectImportBundleJob(Job):
    """ProjectManager.put_project's own returned job: once a project
    upload's definition itself is staged and committed, imports whatever
    sessions.json/tests.json its zip bundled — one entry at a time
    (sessions and test entries share the same pending queue, so a
    single percentage covers both), so a large re-import reports real
    progress instead of blocking."""

    def __init__(
        self, manager: SessionImportManager, db: Db, project_name: str,
        sessions: list[dict], test_entries: list[dict],
    ) -> None:
        super().__init__(key="upload", username=f"upload:{uuid.uuid4().hex}")
        self._manager = manager
        self._db = db
        self._project_name = project_name
        self._sessions = sessions
        self._test_entries = test_entries
        self._revision: int | None = None
        self._edit_count: int | None = None
        self._pending: list[tuple] = []

    def _prepare(self) -> tuple[int, tuple[Job, ...]]:
        if self._sessions:
            self._db.publish_project(self._project_name)
        if self._test_entries:
            self._revision = self._db.get_project_revision(self._project_name)
            self._edit_count = self._db.get_project_draft_edit_count(self._project_name)
        self._pending = (
            [('session', session) for session in self._sessions]
            + [('test', entry) for entry in self._test_entries]
        )
        # A plain upload with no bundled sessions/test results still
        # needs one step to reach is_done() — Job.progress() divides by
        # total_steps, which must never be 0.
        return max(len(self._pending), 1), ()

    @property
    def is_background(self) -> bool:
        return False

    @property
    def result(self) -> str | None:
        return json.dumps({'success': True, 'project_name': self._project_name})

    async def _run_next_step(self) -> None:
        if not self._pending:
            return
        kind, payload = self._pending.pop(0)
        if kind == 'session':
            self._import_session(payload)
        else:
            self._import_test(payload)

    def _import_session(self, session_data: dict) -> None:
        try:
            username = session_data.get('username') or self._db.next_test_user_username(self._project_name)
            self._manager.import_session_json(username, self._project_name, session_data)
        except (ValueError, KeyError, TypeError):
            logger.exception("Skipped a malformed session while importing an uploaded project's sessions.json.")

    def _import_test(self, entry: dict) -> None:
        try:
            self._db.upsert_test_aggregate_result(
                self._project_name, self._revision, self._edit_count,
                entry['kind'], entry.get('target'), entry['strategy'], json.dumps(entry['results']),
            )
        except (KeyError, TypeError):
            logger.exception("Skipped a malformed entry while importing an uploaded project's tests.json.")
