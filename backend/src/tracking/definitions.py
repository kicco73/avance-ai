"""Signal definitions and payload-building for the active project's YAML.
AI-call-shaped logic (computing signal values) lives in
tracking/evaluator.py's SignalEvaluator instead — this class never makes
AI calls itself."""
from __future__ import annotations

from automaton.automaton import Automaton, SignalPayload
from db import Db
from system.logging_factory import LoggerFactory
from system.web_session import WebSession
from tracking.fixed_project_context import ProjectContext

logger = LoggerFactory.get_logger(__name__)

class Signals(object):
    def __init__(self, project_service: ProjectContext, db: Db) -> None:
        self._project_service = project_service
        self._db = db

    @property
    def automaton(self) -> Automaton:
        automaton = self._project_service.get_active_automaton()
        assert automaton is not None
        return automaton

    def _active_project_id(self) -> str:
        project_id = self._db.get_active_project_id(WebSession().user)
        if project_id is None:
            raise ValueError("No active project")
        return project_id

    def get_definition(self, names: set[str] | None = None) -> str:
        """`names` restricts the definitions included — a signal the
        current state's outgoing triggers could never use is skipped,
        saving tokens. Omitted (None) means every declared signal."""
        relevant = self.automaton.signals if names is None else [s for s in self.automaton.signals if s.name in names]
        return "- Definition of signals:\n"+"\n\n".join(
            f'\t- Signal "{s.name}":\n{s.definition}' for s in relevant
        )

    def _snapshot_to_signals_payload(self, snapshot: dict | None) -> list[SignalPayload]:
        """Builds the GET /api/signals response from a persisted snapshot
        (or None). A missing/null value means that signal's computation
        failed — distinct from no snapshot at all (auto-tracking hasn't run)."""
        results = []
        for s in self.automaton.signals:
            if snapshot is None:
                value, error = None, False
            else:
                value = snapshot.get(s.name)
                error = value is None
            results.append({
                "name": s.name,
                "ui_label": s.ui_label,
                "ui_description": s.ui_description,
                "value": value,
                "error": error,
            })
        return results

    def get_latest_signals(self) -> list[SignalPayload]:
        """Read-only, never calls the AI — reports the latest snapshot
        persisted through db.py. Signals are only (re)computed via
        compute_signals(), from the auto-tracking flow."""
        project_id = self._active_project_id()
        signal_snapshot = self._db.get_latest_signal_snapshot(project_id)
        return self._snapshot_to_signals_payload(signal_snapshot)
