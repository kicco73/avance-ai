from __future__ import annotations

import json
from typing import Protocol

from automaton.automaton import Action, Automaton, State
from db.db import Db
from db.models import TestObservation
from events import EnvChanged, StateChanged, publish
from logging_factory import LoggerFactory
from tracking.env import Env
from tracking.evaluation_scope import EvaluationScopeBuilder

logger = LoggerFactory.get_logger(__name__)


class TrackingSink(Protocol):
    """Whatever TrackingEngine needs to persist a signal snapshot/state
    transition — production writes to the real Db (see DbTrackingSink),
    while a test-replay sink can satisfy this independently."""

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
        origin: str | None = None,
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
        origin: str | None = None,
    ) -> int:
        return self._db.save_transition(
            old_state, action, new_state, session_id,
            transition_log_level=transition_log_level,
            signal_values=signal_values,
            message_id=message_id,
            origin=origin,
        )


class TestObservationSink:
    """TrackingSink for a test replay — writes to TestObservation, never
    to Tracking, so a replay can never be mistaken for (or overwrite)
    real production data."""

    def __init__(self, run_id: int) -> None:
        self._run_id = run_id

    def save_signal_snapshot(self, values: dict, session_id: int, message_id: int | None = None) -> int:
        row = TestObservation.create(
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
        origin: str | None = None,
    ) -> int:
        # transition_log_level: received only to satisfy TrackingSink's
        # shared shape — there's no production log to write for a replay.
        row = TestObservation.create(
            run=self._run_id, session=session_id, message=message_id,
            old_state=old_state, action=action, new_state=new_state,
            values=json.dumps(signal_values) if signal_values is not None else None,
        )
        return row.id


class TrackingEngine:
    """Trigger evaluation + transition/env application. No temporal ("as
    of when") concept passes through these methods — the injected
    `env`/`scope_builder` already behaves correctly for its own context."""

    def __init__(
        self, sink: TrackingSink, env: Env, scope_builder: EvaluationScopeBuilder, auto_tracking_enabled: bool = True,
    ) -> None:
        self._sink = sink
        self._env = env
        self._scope_builder = scope_builder
        # "Dev mode: freeze automatic state transitions" toggle — False
        # means a triggerable action is never *selected* here (signals
        # are still evaluated); manual actions bypass this and always fire.
        self._auto_tracking_enabled = auto_tracking_enabled

    def evaluate_triggered_action(self, automaton: Automaton, state: State, signal_values: dict) -> Action | None:
        """None whenever auto-tracking is frozen or `state` has nothing
        triggerable. Only decides which action fires from already-computed
        signals — never whether they get computed at all."""
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
        origin: str,
        username: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """`username`/`project_id`: optional, defaulting to None meaning
        "don't publish" — a test replay has no real user/project of
        its own and must never trigger a StateChanged/EnvChanged a wake-up
        handler could act on. Returns the tracking row id. The fired
        action's own on-enter is scheduled as a task by apply_action_env,
        never returned: it reaches the browser over the websocket."""
        if action is None:
            # No transition fired — just the evaluation itself is worth
            # keeping (see db.get_latest_signal_snapshot, Tracking.values).
            return self._sink.save_signal_snapshot(signal_values, session_id, message_id)

        # Always saved, self-loop or not — a fired trigger is a real event
        # worth a history entry either way; a self-loop just never bumps
        # history_cutoff's own timestamp.

        self.apply_action_env(
            automaton, action, signal_values, state.key, username=username, project_id=project_id, session_id=session_id,
        )
        return self.record_transition(
            automaton, state, action, signal_values, session_id, message_id,
            origin=origin, username=username, project_id=project_id,
        )

    def record_transition(
        self,
        automaton: Automaton,
        state: State,
        action: Action,
        signal_values: dict,
        session_id: int,
        message_id: int | None = None,
        *,
        origin: str,
        username: str | None = None,
        project_id: str | None = None,
    ) -> int:
        # FIXME: caller must have already applied action's own env: (via
        # apply_action_env) itself — calling apply_transition too for the
        # same action would run apply_action_env (and any on-enter) twice.
        tracking_id = self._sink.save_transition(
            state.key,
            action.name,
            action.target,
            session_id,
            transition_log_level=automaton.get_state(action.target).transition_log_level,
            signal_values=signal_values,
            message_id=message_id,
            origin=origin,
        )
        self.notify_transition(username, project_id, state.key, action.target)
        return tracking_id

    @staticmethod
    def notify_transition(
        username: str | None, project_id: str | None, old_state: str, new_state: str
    ) -> None:
        """Publishes StateChanged for a *real* transition only (old_state
        != new_state) — a no-op when either identity is missing. Called
        right after save_transition, from both the auto-tracking and manual-action paths."""
        if username is None or project_id is None:
            return
        if old_state == new_state:
            return
        publish(StateChanged(username=username, project_id=project_id, from_state=old_state, to_state=new_state))

    def apply_action_env(
        self,
        automaton: Automaton,
        action: Action,
        signal_values: dict,
        state_key: str,
        *,
        username: str | None = None,
        project_id: str | None = None,
        session_id: int | None = None,
    ) -> None:
        """Applies `action`'s own `env:` updates to the current scope —
        shared by both the auto-tracking and manual-action paths (the
        latter fires with empty signal_values). Publishes one EnvChanged
        per key actually written. Then hands `action.on_enter` (§6.5's
        actuator.* calls) to the scope's own actuator set, which runs it
        as an OnEnterTask due now — never inline here: actuator.prompt
        is a model call, send_mail a network call, and the browser gets
        whatever they produce over the websocket (see
        tracking/actuators/on_enter_task.py). `session_id`: the firing
        session, for the OnEnterTask itself."""
        if not action.env and not action.on_enter:
            return
        scope = self._scope_builder.build(automaton, state_key, signal_values)
        if action.env:
            updates = automaton.eval_action_env(action, scope)
            if updates:
                self._env.update_action_set(updates)
                if username is not None and project_id is not None:
                    for key, value in updates.items():
                        publish(EnvChanged(username=username, project_id=project_id, key=key, value=value))
        if action.on_enter:
            scope["actuator"].schedule_on_enter(action, scope, session_id=session_id)

    def schedule_on_enter(
        self, automaton: Automaton, action: Action, state_key: str, session_id: int | None = None,
    ) -> None:
        """`action.on_enter` scheduled against a fresh scope, with no env
        applied — for a caller firing an action's on-enter outside a
        real transition/env-apply path (a brand-new session's own
        init-action, a test-session reset), where env: is either
        irrelevant or already handled elsewhere."""
        if not action.on_enter:
            return
        scope = self._scope_builder.build(automaton, state_key, None)
        scope["actuator"].schedule_on_enter(action, scope, session_id=session_id)
