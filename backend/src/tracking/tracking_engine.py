from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from automaton.automaton import Action, Automaton, State
from db.db import Db
from db.models import BenchmarkRunObservation
from events import EnvChanged, StateChanged, publish
from tracking.env import Env
from tracking.evaluation_scope import EvaluationScopeBuilder

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


class BenchmarkRunObservationSink:
    """TrackingSink for a benchmark replay — writes to
    BenchmarkRunObservation, never to Tracking: a replay must never be
    mistaken for (or overwrite) real production data. Talks to the
    peewee model directly rather than through Db, unlike DbTrackingSink:
    it owns exactly one table for exactly one run's lifetime, no other
    Db concern (connection management, schema checks, ...) applies."""

    def __init__(self, run_id: int) -> None:
        self._run_id = run_id

    def save_signal_snapshot(self, values: dict, session_id: int, message_id: int | None = None) -> int:
        row = BenchmarkRunObservation.create(
            run=self._run_id, session=session_id, message=message_id, values=json.dumps(values),
        )
        return row.id

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
        # transition_log_level: received only to satisfy TrackingSink's
        # shared shape — there's no production log to write for a replay.
        row = BenchmarkRunObservation.create(
            run=self._run_id, session=session_id, message=message_id,
            old_state=old_state, action=action, new_state=new_state,
            values=json.dumps(signal_values) if signal_values is not None else None,
        )
        return row.id


class TrackingEngine:
    """Trigger evaluation + transition/env application — extracted
    verbatim from tracking_processor.py's own (formerly _would_trigger_
    action/_move_automaton/_apply_action_env). No temporal ("as of when")
    concept ever passes through these methods: whoever constructs this
    (production or a benchmark replay) injects an `env`/`scope_builder`
    already behaving correctly for its own context, refreshed turn by
    turn by whoever orchestrates the loop — never through these method
    signatures themselves."""

    def __init__(
        self, sink: TrackingSink, env: Env, scope_builder: EvaluationScopeBuilder, auto_tracking_enabled: bool = True,
    ) -> None:
        self._sink = sink
        self._env = env
        self._scope_builder = scope_builder
        # EditProjectView.vue's own "Dev mode: freeze automatic state
        # transitions" toggle (see TrackingService.is_auto_tracking_enabled,
        # per-test-session, threaded down through TrackingProcessor.
        # __init__) — False means
        # a triggerable action is never *selected* here, so apply_transition
        # always falls into its own action-is-None branch below and just
        # logs the evaluation (see evaluate_triggered_action's own
        # docstring: signals are computed and evaluated exactly as before
        # either way, only whether the result actually moves the
        # conversation is what this gates). Manual (button) actions never
        # go through this method at all (see ChatService.apply_manual_
        # action, which calls apply_action_env directly) — frozen or not,
        # a manual action always fires.
        self._auto_tracking_enabled = auto_tracking_enabled

    def evaluate_triggered_action(self, automaton: Automaton, state: State, signal_values: dict) -> Action | None:
        """None whenever auto-tracking is frozen (see __init__'s own
        docstring) or `state` has nothing triggerable to begin with —
        both cases leave signal_values completely untouched: this method
        only ever decides which action (if any) fires from already-
        computed signals, never whether they get computed at all (that
        already happened by the time this runs — see tracking_processor_
        ai.py/tracking_processor_user.py's own on_receiving_metadata_*
        callbacks, which call this only after parsing the turn's own
        [signals] tag)."""
        if not self._auto_tracking_enabled or not state.has_triggerable_actions:
            return None

        scope = self._scope_builder.build(automaton, state.key, signal_values)
        return automaton.evaluate_triggers_action(state.key, scope)

    def apply_transition(
        self,
        automaton: Automaton,
        state: State,
        action: Action | None,
        signal_values: dict,
        session_id: int,
        message_id: int | None = None,
        *,
        username: str | None = None,
        project_name: str | None = None,
    ) -> int:
        """`username`/`project_name`: whose transition this is, for
        notify_transition/apply_action_env's own event publishing below
        — optional (defaulting to None, meaning "don't publish") since
        the one caller with no real user/project of its own (a benchmark
        replay, see metrics/benchmark_processor.py) must never publish a
        StateChanged/EnvChanged a wake-up handler could act on: a replay
        is not a real turn, and must never affect real cross-project
        state."""
        if action is None:
            # No transition fired — just the evaluation itself is worth
            # keeping (see db.get_latest_signal_snapshot, Tracking.values).
            return self._sink.save_signal_snapshot(signal_values, session_id, message_id)

        # Always saved, self-loop or not — a fired trigger is a real event
        # worth a history entry either way. A self-loop just never bumps
        # history_cutoff's timestamp (see db.get_last_transition_timestamp).
        # The full evaluated values ride along on this same row (see
        # db.py's Tracking) instead of a separate snapshot row to link to.

        self.apply_action_env(automaton, action, signal_values, state.key, username=username, project_name=project_name)
        tracking_id = self._sink.save_transition(
            state.key,
            action.name,
            action.target,
            session_id,
            transition_log_level=automaton.get_state(action.target).transition_log_level,
            signal_values=signal_values,
            message_id=message_id,
        )
        self.notify_transition(username, project_name, state.key, action.target)
        return tracking_id

    @staticmethod
    def notify_transition(
        username: str | None, project_name: str | None, old_state: str, new_state: str
    ) -> None:
        """Publishes StateChanged for a *real* transition only — same
        self-loop-excluded criterion db.tracking._latest_transition's own
        real_only already uses for history_cutoff (old_state !=
        new_state). Called right after save_transition, from both
        apply_transition above (the auto-tracking path) and
        ProjectService.apply_manual_action (which saves its own
        transition directly, bypassing apply_transition entirely — see
        that method's own docstring) — the two places a transition is
        ever actually persisted. A no-op when either identity is missing
        (see apply_transition's own docstring on why)."""
        if username is None or project_name is None:
            return
        if old_state == new_state:
            return
        publish(StateChanged(username=username, project_name=project_name, from_state=old_state, to_state=new_state))

    def apply_action_env(
        self,
        automaton: Automaton,
        action: Action,
        signal_values: dict,
        state_key: str,
        *,
        username: str | None = None,
        project_name: str | None = None,
    ) -> None:
        """Applies `action`'s own `env:` updates to the current scope —
        shared by both the auto-tracking path (apply_transition, above)
        and ChatService.apply_manual_action's manual-action path (see
        chat/chat_service.py), which fires this directly with an empty
        signal_values since no AI computation runs for a manual action,
        and the *origin* state's own key (the one the action fired from
        — matches evaluate_triggered_action's own scoping, and is what
        merge_if_referenced's "any triggerable action leaving here"
        check needs). `username`/`project_name`: see apply_transition's
        own docstring — publishes one EnvChanged per key actually
        written, right after update_action_set."""
        if not action.env:
            return
        scope = self._scope_builder.build(automaton, state_key, signal_values)
        updates = automaton.eval_action_env(action, scope)
        if updates:
            self._env.update_action_set(updates)
            if username is not None and project_name is not None:
                for key, value in updates.items():
                    publish(EnvChanged(username=username, project_name=project_name, key=key, value=value))
