from __future__ import annotations

import logging
from typing import Any, Protocol

from automaton.automaton import Action, Automaton, State
from db.db import Db
from metrics.metric_service import MetricsProvider
from tracking.env import Env
from tracking.evaluator import SignalEvaluator

logger = logging.getLogger(__name__)


class TrackingSink(Protocol):
    """Whatever TrackingEngine needs to persist a signal snapshot/state
    transition — production writes to the real Db (see DbTrackingSink);
    a benchmark-replay sink (not introduced here) can satisfy this the
    same way, without either depending on the other."""

    def save_signal_snapshot(self, values: dict, session_id: int, message_id: int | None = None) -> int:
        ...

    def save_transition(
        self,
        old_state: str,
        action: str,
        new_state: str,
        session_id: int,
        transition_log_level: str,
        signal_values: dict | None = None,
        message_id: int | None = None,
    ) -> int:
        ...


class DbTrackingSink:
    """TrackingSink backed by the real Db — production's own sink."""

    def __init__(self, db: Db) -> None:
        self._db = db

    def save_signal_snapshot(self, values: dict, session_id: int, message_id: int | None = None) -> int:
        return self._db.save_signal_snapshot(values, session_id, message_id)

    def save_transition(
        self,
        old_state: str,
        action: str,
        new_state: str,
        session_id: int,
        transition_log_level: str,
        signal_values: dict | None = None,
        message_id: int | None = None,
    ) -> int:
        return self._db.save_transition(
            old_state, action, new_state, session_id,
            transition_log_level=transition_log_level,
            signal_values=signal_values,
            message_id=message_id,
        )


class TrackingEngine:
    """Trigger evaluation + transition/env application — extracted
    verbatim from tracking_processor.py's own (formerly _would_trigger_
    action/_move_automaton/_apply_action_env). No temporal ("as of when")
    concept ever passes through these methods: whoever constructs this
    (production or a benchmark replay) injects an `env`/`metrics` already
    behaving correctly for its own context, refreshed turn by turn by
    whoever orchestrates the loop — never through these method
    signatures themselves."""

    def __init__(self, sink: TrackingSink, env: Env, metrics: MetricsProvider) -> None:
        self._sink = sink
        self._env = env
        self._metrics = metrics

    def evaluate_triggered_action(self, automaton: Automaton, state: State, signal_values: dict) -> Action | None:
        signal_evaluator = SignalEvaluator()

        if not state.has_triggerable_actions:
            return None

        needed_signal_names = automaton.triggerable_signal_names(state.key)
        signal_values = signal_evaluator.validate(automaton, signal_values, needed_signal_names)

        evaluation_names = self._metrics.merge_if_referenced(automaton, state.key, signal_values)
        evaluation_names = self._env.merge_if_referenced(automaton, state.key, evaluation_names)
        triggered_action = automaton.evaluate_triggers_action(state.key, evaluation_names)

        return triggered_action

    def apply_transition(
        self,
        automaton: Automaton,
        state: State,
        action: Action | None,
        signal_values: dict,
        session_id: int,
        message_id: int | None = None,
    ) -> int:
        if action is None:
            # No transition fired — just the evaluation itself is worth
            # keeping (see db.get_latest_signal_snapshot, Tracking.values).
            return self._sink.save_signal_snapshot(signal_values, session_id, message_id)

        # Always saved, self-loop or not — a fired trigger is a real event
        # worth a history entry either way. A self-loop just never bumps
        # history_cutoff's timestamp (see db.get_last_transition_timestamp).
        # The full evaluated values ride along on this same row (see
        # db.py's Tracking) instead of a separate snapshot row to link to.

        self.apply_action_env(automaton, action, signal_values)
        return self._sink.save_transition(
            state.key,
            action.name,
            action.target,
            session_id,
            transition_log_level=automaton.get_state(action.target).transition_log_level,
            signal_values=signal_values,
            message_id=message_id,
        )

    def apply_action_env(self, automaton: Automaton, action: Action, signal_values: dict) -> None:
        """Applies `action`'s own `env:` updates to the current scope —
        shared by both the auto-tracking path (apply_transition, above)
        and ChatService.apply_manual_action's manual-action path (see
        chat/chat_service.py), which fires this directly with an empty
        signal_values since no AI computation runs for a manual action."""
        if not action.env:
            return
        scope = {**signal_values, **self._metrics.calculate_values(), **self._env.to_dict()}
        updates = automaton.eval_action_env(action, scope)
        if updates:
            self._env.update_action_set(updates)
