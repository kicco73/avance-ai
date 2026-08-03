"""Auto-tracking: gets signal values (embedded in an already-generated
reply, or via a TurnStrategy's own explicit fallback call, see chat.
turn_strategy.TurnStrategy.compute_explicitly), evaluates triggers, and
applies the resulting transition (self-loops excluded) — never generates
a message itself. Message generation for a transition (action_prompt,
opening message) stays in ChatService, since a manual action needs it
too and never runs through here.
"""
from __future__ import annotations

import logging

from automaton.automaton import Action, Automaton, State
from ai.ai_service import AiService
from chat.env import Env
from chat.priming import build_priming_messages
from chat.turn_strategy_builder import build_turn_strategy
from metrics.metric_service import MetricService
from tracking.definitions import Signals
from tracking.evaluator import SignalEvaluator
from db import Db

logger = logging.getLogger(__name__)


class AutoTracker(object):
    def __init__(
        self, db: Db, ai_service: AiService, signals: Signals, metrics: MetricService, env: Env,
        signal_evaluator: SignalEvaluator,
    ) -> None:
        self._db = db
        self._ai_service = ai_service
        self._signals = signals
        self._metrics = metrics
        self._env = env
        self._signal_evaluator = signal_evaluator

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
        state, the id of the Tracking row the evaluation itself persisted —
        see TrackingService.run_auto_tracking, which links that row back to
        whichever message caused this call)."""
        if not state.has_triggerable_actions:
            return None, state, None

        # The only signals a trigger leaving this state could ever use —
        # see Automaton.triggerable_signal_names. Scopes both the
        # embedded validation and the explicit fallback call below down
        # to exactly this set, instead of every signal the project
        # declares, since anything outside it can't affect
        # evaluate_triggers below no matter what value it takes.
        needed_signal_names = automaton.triggerable_signal_names(state.key)
        if signal_values:
            # Embedded — a reply was already generated for some other
            # reason and already reported these (see MetadataHandler.
            # signal_values) — still validated the same way an explicit
            # computation's own reply would be (see SignalEvaluator).
            signal_values = self._signal_evaluator.validate(automaton, signal_values, needed_signal_names)
        else:
            # No reply to piggyback on at all — fall back to a dedicated
            # call, in whichever concrete TurnStrategy's own dialect is
            # currently active (tags for v1, on_metadata for v2 — see
            # TurnStrategy.compute_explicitly's own docstring for why
            # this can't be provider-agnostic the way validate() above
            # is; a v1 provider's own reply never carries structured
            # metadata, a v2 provider's own reply never carries tags).
            logger.warning("AutoTracker.run(): signals not found in metadata, falling back to AI")
            since = self._history_cutoff(project_name, state)
            signal_definition = self._signals.get_definition(needed_signal_names)
            relevant_signals = (
                automaton.signals if needed_signal_names is None
                else [s for s in automaton.signals if s.name in needed_signal_names]
            )
            # Each signal brings only its own attachments into this call —
            # never a state's or general_prompt's (different scope entirely).
            signal_attachments = [a for s in relevant_signals for a in s.attachments.values()]
            priming_messages = build_priming_messages(signal_attachments)
            call_history = priming_messages + self._signals.history_window(session_id, pending_message, since)
            strategy = build_turn_strategy(self._ai_service, wants_streaming=False)
            raw_values = await strategy.compute_explicitly(signal_definition, self._env, call_history)
            signal_values = self._signal_evaluator.validate(automaton, raw_values, needed_signal_names)
        # Metrics/env are merged only for this evaluation, never persisted:
        # signal_values below (save_signal_snapshot/save_transition) stays
        # exactly what auto-tracking actually observed — mixing metric/env
        # values into that log would corrupt SignalStabilityMetric's own
        # future readings, which trusts every numeric key there to be a
        # real domain signal (see metrics_framework/metrics/signal_stability.py).
        evaluation_names = self._metrics.merge_if_referenced(automaton, state.key, signal_values)
        evaluation_names = self._env.merge_if_referenced(automaton, state.key, evaluation_names)
        triggered_action = automaton.evaluate_triggers(state.key, evaluation_names)
        if triggered_action is None:
            # No transition fired — just the evaluation itself is worth
            # keeping (see db.get_latest_signal_snapshot, Tracking.values).
            signal_row_id = self._db.save_signal_snapshot(signal_values, session_id)
            return None, state, signal_row_id

        action = automaton.move(state.key, triggered_action)
        # Always saved, self-loop or not — a fired trigger is a real event
        # worth a history entry either way. A self-loop just never bumps
        # history_cutoff's timestamp (see db.get_last_transition_timestamp).
        # The full evaluated values ride along on this same row (see
        # db.py's Tracking) instead of a separate snapshot row to link to.
        signal_row_id = self._db.save_transition(
            state.key,
            triggered_action,
            action.target,
            session_id,
            transition_log_level=automaton.get_state(action.target).transition_log_level,
            signal_values=signal_values,
        )

        self._apply_action_env(automaton, action, signal_values)

        new_state = automaton.get_state(action.target)
        return action, new_state, signal_row_id

    def _apply_action_env(self, automaton: Automaton, action: Action, signal_values: dict) -> None:
        """The fired action's own `env` field (see automaton_builder.py's
        _build_action/Automaton.eval_action_env), evaluated and merged
        onto chat.env.Env's persisted store — a no-op, at no extra cost,
        for the overwhelmingly common action with no `env` field at all.
        Rebuilt fresh rather than reusing `evaluation_names` (this run's
        own merge_if_referenced calls above, gated on the *state's*
        triggers referencing metrics/env): an env expression can
        reference either even when nothing in this state's triggers do,
        and needs the current *stored* env values too (e.g. `count + 1`
        reading `count`'s own previous value) which trigger evaluation
        itself never merges in."""
        if not action.env:
            return
        scope = {**signal_values, **self._metrics.calculate_values(), **self._env.to_dict()}
        updates = automaton.eval_action_env(action, scope)
        if updates:
            self._env.update_action_set(updates)
