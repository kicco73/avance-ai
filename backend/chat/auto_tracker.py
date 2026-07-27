"""Auto-tracking: computes/falls back on signal values, evaluates
triggers, and applies the resulting transition (self-loops excluded) —
never generates a message itself. Message generation for a transition
(action_prompt, opening message) stays in ChatService, since a manual
action needs it too and never runs through here.
"""
from __future__ import annotations

import logging

from automaton.automaton import Action, Automaton, State, trigger_signal_names
from ai.ai_service import AiService
from chat.priming import build_priming_messages
from chat.signals import Signals
from db import Db

logger = logging.getLogger(__name__)


class AutoTracker(object):
    def __init__(self, db: Db, ai_service: AiService, signals: Signals) -> None:
        self._db = db
        self._ai_service = ai_service
        self._signals = signals

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
        automaton: Automaton,
        state: State,
        signal_values: dict | None,
    ) -> tuple[Action | None, State]:
        """Returns (None, state) if nothing fired, else (action, the
        state it landed on) — with the transition already persisted."""
        if not state.has_triggerable_actions:
            return None, state

        if not signal_values:
            # fallback, we need to call AI to compute values
            logger.warning("AutoTracker.run(): signals not found in metadata, falling back to AI")
            since = self._history_cutoff(project_name, state)
            signal_values = await self._signals.compute(
                self._ai_service, build_priming_messages, pending_message, since=since
            )
        # Saved before trigger evaluation so a fired transition can reference
        # the exact snapshot id that caused it.
        snapshot_id = self._db.save_signal_snapshot(signal_values, project_name)

        triggered_action = automaton.evaluate_triggers(state.key, signal_values)
        if triggered_action is None:
            return None, state

        action = automaton.move(state.key, triggered_action)
        relevant_names = trigger_signal_names(action.trigger)
        relevant_values = {n: signal_values.get(n) for n in relevant_names}
        # Always saved, self-loop or not — a fired trigger is a real event
        # worth a history entry either way. A self-loop just never bumps
        # history_cutoff's timestamp (see db.get_last_transition_timestamp).
        self._db.save_transition(
            state.key,
            triggered_action,
            action.target,
            project_name,
            transition_log_level=automaton.get_state(action.target).transition_log_level,
            signal_snapshot_id=snapshot_id,
            signal_values=relevant_values,
        )

        new_state = automaton.get_state(action.target)
        return action, new_state
