"""Signal definitions and payload-building for the active project's YAML
— get_active_automaton and db are constructor-injected. Instantiated as
TrackingService's own internal `_definitions` (see tracking_service.py).
AI-call-shaped logic (computing signal values, either embedded in a
normal turn's own reply or via a dedicated fallback call) lives in
tracking/evaluator.py's SignalEvaluator instead — this class no longer
makes AI calls itself (see its own module docstring for why the old
standalone prompt here was deprecated in favor of the embedded
[signals]-tag convention)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from automaton.automaton import Automaton, SignalPayload
from db import Db

logger = logging.getLogger(__name__)

# How many recent history messages to send the model for a signals
# computation call. Kept even so a slice always starts on a "user" turn
# (history strictly alternates user/assistant, in pairs).
SIGNALS_HISTORY_WINDOW = 14

# Supplies the currently-active Automaton — constructor-injected rather
# than imported: this module doesn't own which project is active.
GetActiveAutomaton = Callable[[], Automaton]

class Signals(object):
    def __init__(self, get_active_automaton: GetActiveAutomaton, db: Db) -> None:
        self._get_active_automaton = get_active_automaton
        self._db = db

    @property
    def automaton(self) -> Automaton:
        return self._get_active_automaton()

    def _active_project_name(self) -> str:
        name = self._db.get_active_project_name()
        if name is None:
            raise ValueError("No active project")
        return name

    def history_window(
        self, session_id: int, pending_message: dict | None, since: datetime | None
    ) -> list[dict]:
        """Recent messages as a single 'evaluate this transcript' turn —
        not multi-turn history, which invites the model to keep chatting.
        `pending_message` is appended locally, unpersisted. Used by
        SignalEvaluator.compute_explicitly's own dedicated call."""
        fetch_n = SIGNALS_HISTORY_WINDOW - 1 if pending_message is not None else SIGNALS_HISTORY_WINDOW
        recent = self._db.get_messages(session_id, last_n=fetch_n, since=since)
        if pending_message is not None:
            recent = recent + [pending_message]
        if recent and recent[0]["role"] != "user":
            recent = recent[1:]
        transcript = "\n".join(f"[{m['timestamp']}] {m['role']}: {m['content']}" for m in recent)
        return [{"role": "user", "content": f"Conversation transcript:\n\n{transcript}"}]

    def get_definition(self, names: set[str] | None = None) -> str:
        """`names` (see Automaton.triggerable_signal_names) restricts the
        definitions actually included — the auto-tracking prompt's own
        scoping optimization (see tracking/evaluator.py): a signal the
        current state's own outgoing triggers could never use is simply
        never asked for, saving both the definition's own tokens and
        whatever the model would otherwise spend computing it. Omitted
        (None) means every declared signal, unchanged from before this
        existed."""
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
