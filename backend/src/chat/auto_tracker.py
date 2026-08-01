"""Auto-tracking: computes/falls back on signal values, evaluates
triggers, and applies the resulting transition (self-loops excluded) —
never generates a message itself. Message generation for a transition
(action_prompt, opening message) stays in ChatService, since a manual
action needs it too and never runs through here.
"""
from __future__ import annotations

import logging

from automaton.automaton import Action, Automaton, State
from ai.ai_service import AiService
from chat.metrics_service import ChatMetrics
from chat.priming import build_priming_messages
from chat.signals import Signals
from db import Db

logger = logging.getLogger(__name__)


class AutoTracker(object):
    def __init__(self, db: Db, ai_service: AiService, signals: Signals, metrics: ChatMetrics) -> None:
        self._db = db
        self._ai_service = ai_service
        self._signals = signals
        self._metrics = metrics

    def _history_cutoff(self, project_name: str, state: State):
        # Same rule as ChatService._history_cutoff: history_cutoff states
        # exclude anything at or before the last transition.
        if not state.history_cutoff:
            return None
        return self._db.get_last_transition_timestamp(project_name)

    async def run(
        self,
        pending_message: dict | None,
        project_name: str,
        session_id: int,
        automaton: Automaton,
        state: State,
        signal_values: dict | None,
    ) -> tuple[Action | None, State, int | None]:
        """Returns (None, state, None) if nothing was even evaluated (no
        triggerable action to check), else (action or None, the resulting
        state, the id of the Signals row the evaluation itself persisted —
        see ChatService._run_auto_tracking, which links that row back to
        whichever message caused this call)."""
        if not state.has_triggerable_actions:
            return None, state, None

        if not signal_values:
            # fallback, we need to call AI to compute values
            logger.warning("AutoTracker.run(): signals not found in metadata, falling back to AI")
            since = self._history_cutoff(project_name, state)
            signal_values = await self._signals.compute(
                self._ai_service, build_priming_messages, session_id, pending_message, since=since
            )
        # Metrics are merged only for this evaluation, never persisted:
        # signal_values below (save_signal_snapshot/save_transition) stays
        # exactly what auto-tracking actually observed — mixing metric
        # values into that log would corrupt SignalStabilityMetric's own
        # future readings, which trusts every numeric key there to be a
        # real domain signal (see metrics_framework/metrics/signal_stability.py).
        evaluation_names = self._metrics.merge_if_referenced(automaton, state.key, signal_values)
        triggered_action = automaton.evaluate_triggers(state.key, evaluation_names)
        if triggered_action is None:
            # No transition fired — just the evaluation itself is worth
            # keeping (see db.get_latest_signal_snapshot, Signals.values).
            signal_row_id = self._db.save_signal_snapshot(signal_values, session_id)
            return None, state, signal_row_id

        action = automaton.move(state.key, triggered_action)
        # Always saved, self-loop or not — a fired trigger is a real event
        # worth a history entry either way. A self-loop just never bumps
        # history_cutoff's timestamp (see db.get_last_transition_timestamp).
        # The full evaluated values ride along on this same row (see
        # db.py's Signals) instead of a separate snapshot row to link to.
        signal_row_id = self._db.save_transition(
            state.key,
            triggered_action,
            action.target,
            session_id,
            transition_log_level=automaton.get_state(action.target).transition_log_level,
            signal_values=signal_values,
        )

        new_state = automaton.get_state(action.target)
        return action, new_state, signal_row_id
