"""Signal definitions and payload-building for the active project's YAML.
AI-call-shaped logic (computing signal values) lives in
tracking/evaluator.py's SignalEvaluator instead — this class never makes
AI calls itself."""
from __future__ import annotations

import logging
from datetime import datetime

from automaton.automaton import Automaton, SignalPayload
from db import Db
from project.project_service import ProjectService

logger = logging.getLogger(__name__)

# How many recent history messages to send the model for a signals
# computation call. Kept even so a slice always starts on a "user" turn
# (history strictly alternates user/assistant, in pairs).
SIGNALS_HISTORY_WINDOW = 14

class Signals(object):
    def __init__(self, project_service: ProjectService, db: Db) -> None:
        self._project_service = project_service
        self._db = db

    @property
    def automaton(self) -> Automaton:
        return self._project_service.get_active_automaton_and_state()[0]

    def _active_project_name(self) -> str:
        name = self._db.get_active_project_name()
        if name is None:
            raise ValueError("No active project")
        return name

    def history_window(
        self, session_id: int, pending_message: dict | None, since: datetime | None
    ) -> list[dict]:
        """Recent messages framed as a single 'evaluate this transcript'
        turn rather than multi-turn history, which would invite the model
        to keep chatting. `pending_message` is appended locally, unpersisted."""
        fetch_n = SIGNALS_HISTORY_WINDOW - 1 if pending_message is not None else SIGNALS_HISTORY_WINDOW
        recent = self._db.get_messages(session_id, last_n=fetch_n, since=since)
        if pending_message is not None:
            recent = recent + [pending_message]
        if recent and recent[0]["role"] != "user":
            recent = recent[1:]
        transcript = "\n".join(f"[{m['timestamp']}] {m['role']}: {m['content']}" for m in recent)
        return [{"role": "user", "content": f"Conversation transcript:\n\n{transcript}"}]

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
        project_name = self._active_project_name()
        signal_snapshot = self._db.get_latest_signal_snapshot(project_name)
        return self._snapshot_to_signals_payload(signal_snapshot)
