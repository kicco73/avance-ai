from __future__ import annotations

import json
import uuid

from pydantic import ValidationError

from db import Db
from jobs import Job
from schemas import SessionImportJsonRequest
from tracking.session_import import SessionImportManager


class SessionImportJob(Job):

    def __init__(
        self, manager: SessionImportManager, db: Db, project_name: str, uploads: list[tuple[str, bytes]],
    ) -> None:
        super().__init__(key="import", username=f"import:{uuid.uuid4().hex}")
        self._manager = manager
        self._db = db
        self._project_name = project_name
        self._uploads = uploads
        self._pending: list[tuple] = []
        self._results: list[dict] = []
        self._last_session_id: int | None = None
        self._transcript_test_user: str | None = None

    def _prepare(self) -> tuple[int, tuple[Job, ...]]:
        for filename, content in self._uploads:
            if (filename or '').lower().endswith('.json'):
                self._pending.extend(self._parse_json_upload(filename, content))
            else:
                self._pending.append(('transcript', filename, content))
        return len(self._pending), ()

    @property
    def is_background(self) -> bool:
        return False

    @property
    def result(self) -> str | None:
        return json.dumps({'results': self._results, 'last_session_id': self._last_session_id})

    async def _run_next_step(self) -> None:
        kind, *rest = self._pending.pop(0)
        if kind == 'json':
            label, session_data = rest
            self._import_json_session(label, session_data)
        else:
            filename, content = rest
            self._import_transcript(filename, content)

    def _parse_json_upload(self, filename: str, content: bytes) -> list[tuple]:
        try:
            parsed = json.loads(content.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._results.append({'file': filename, 'ok': False, 'error': f'Invalid JSON: {exc}'})
            return []
        if not isinstance(parsed, list):
            self._results.append({'file': filename, 'ok': False, 'error': 'Expected a JSON array of sessions.'})
            return []
        units = []
        for index, session_data in enumerate(parsed):
            label = session_data.get('name') if isinstance(session_data, dict) else None
            label = label or f'{filename} #{index + 1}'
            units.append(('json', label, session_data))
        return units

    def _import_json_session(self, label: str, session_data: dict) -> None:
        try:
            validated = SessionImportJsonRequest(**session_data)
            username = validated.username or self._db.next_test_user_username(self._project_name)
            session_id = self._manager.import_session_json(username, self._project_name, validated.model_dump())
            self._results.append({'file': label, 'ok': True, 'session_id': session_id})
            self._last_session_id = session_id
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            self._results.append({'file': label, 'ok': False, 'error': str(exc)})

    def _import_transcript(self, filename: str, content: bytes) -> None:
        try:
            if self._transcript_test_user is None:
                self._transcript_test_user = self._db.next_test_user_username(self._project_name)
            session_id = self._manager.import_transcript(
                self._transcript_test_user, self._project_name, content.decode('utf-8'), title=filename,
            )
            self._results.append({'file': filename, 'ok': True, 'session_id': session_id})
            self._last_session_id = session_id
        except (ValueError, UnicodeDecodeError) as exc:
            self._results.append({'file': filename, 'ok': False, 'error': str(exc)})
