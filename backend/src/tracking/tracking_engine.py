from __future__ import annotations

import json
from typing import Protocol

from automaton.automaton import Action, Automaton, State
from automaton.choice import ChoiceSelection
from db.db import Db
from db.models import TestObservation
from system.logging_factory import LoggerFactory
from tracking.env import Env
from tracking.evaluation_scope import EvaluationScopeBuilder

logger = LoggerFactory.get_logger(__name__)


class TrackingSink(Protocol):
    """Whatever TrackingEngine needs to persist a signal snapshot/state
    transition — production writes to the real Db (see DbTrackingSink),
    while a test-replay sink can satisfy this independently."""

    def save_signal_snapshot(
        self, values: dict, session_id: int, message_id: int | None = None, output_values: dict | None = None,
    ) -> int:
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
        output_values: dict | None = None,
    ) -> int:
        ...


class DbTrackingSink:
    """TrackingSink backed by the real Db — production's own sink."""

    def __init__(self, db: Db) -> None:
        self._db = db

    def save_signal_snapshot(
        self, values: dict, session_id: int, message_id: int | None = None, output_values: dict | None = None,
    ) -> int:
        return self._db.save_signal_snapshot(values, session_id, message_id, output_values=output_values)

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
        output_values: dict | None = None,
    ) -> int:
        return self._db.save_transition(
            old_state, action, new_state, session_id,
            transition_log_level=transition_log_level,
            signal_values=signal_values,
            message_id=message_id,
            origin=origin,
            output_values=output_values,
        )


class TestObservationSink:
    """TrackingSink for a test replay — writes to TestObservation, never
    to Tracking, so a replay can never be mistaken for (or overwrite)
    real production data."""

    def __init__(self, run_id: int) -> None:
        self._run_id = run_id

    def save_signal_snapshot(
        self, values: dict, session_id: int, message_id: int | None = None, output_values: dict | None = None,
    ) -> int:
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
        output_values: dict | None = None,
    ) -> int:
        row = TestObservation.create(
            run=self._run_id, session=session_id, message=message_id,
            old_state=old_state, action=action, new_state=new_state,
            values=json.dumps(signal_values) if signal_values is not None else None,
        )
        return row.id


class KeepAiMemory:
    def on_entry(self, env: Env) -> None:
        return None


class ClearAiMemory:
    def on_entry(self, env: Env) -> None:
        env.clear_memory()


AI_MEMORY_STRATEGIES = {"keep": KeepAiMemory(), "clear": ClearAiMemory()}


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
        self._auto_tracking_enabled = auto_tracking_enabled

    def evaluate_triggered_action(
        self, automaton: Automaton, state: State, signal_values: dict, selection: ChoiceSelection,
        session_id: int | None = None, output_values: dict | None = None,
    ) -> Action | None:
        """None whenever auto-tracking is frozen or `state` has nothing
        triggerable. Only decides which action fires from already-computed
        signals — never whether they get computed at all. `session_id`:
        see EvaluationScopeBuilder.build. `output_values`: structured output
        dict from this turn's AI generation, available in trigger expressions."""
        if not self._auto_tracking_enabled or not state.has_triggerable_actions:
            return None

        scope = self._scope_builder.build(
            automaton, state.key, signal_values, selection, session_id=session_id, output_values=output_values,
        )
        return automaton.evaluate_triggers_action(state.key, scope)

    def evaluate_choice(
        self, automaton: Automaton, state_key: str, selection: ChoiceSelection, session_id: int,
    ) -> Action | None:
        scope = self._scope_builder.build(automaton, state_key, None, selection, session_id=session_id)
        return automaton.evaluate_triggers_action(state_key, scope)

    def apply_transition(
        self,
        automaton: Automaton,
        state: State,
        action: Action | None,
        signal_values: dict | None,
        selection: ChoiceSelection,
        session_id: int,
        message_id: int | None = None,
        *,
        origin: str,
        username: str | None = None,
        project_id: str | None = None,
        output_values: dict | None = None,
    ) -> tuple[int, dict]:
        """Returns the tracking row id and the env keys the fired action
        wrote — the second so whoever ran the turn can say so on the way
        out (see turn/outbound.py). The fired action's own task is
        scheduled as a task by apply_action_env, never returned: it
        reaches the browser over the websocket. `output_values`:
        structured output dict from this turn's AI generation."""
        if action is None:
            return self._sink.save_signal_snapshot(
                signal_values, session_id, message_id, output_values=output_values,
            ), {}

        written = self.apply_action_env(
            automaton, action, signal_values, selection, state.key, username=username, project_id=project_id,
            session_id=session_id, output_values=output_values,
        )
        return self.record_transition(
            automaton, state, action, signal_values, session_id, message_id,
            origin=origin, username=username, project_id=project_id, output_values=output_values,
        ), written

    def record_transition(
        self,
        automaton: Automaton,
        state: State,
        action: Action,
        signal_values: dict | None,
        session_id: int,
        message_id: int | None = None,
        *,
        origin: str,
        username: str | None = None,
        project_id: str | None = None,
        output_values: dict | None = None,
    ) -> int:
        # FIXME: caller must have already applied action's own env: (via
        tracking_id = self._sink.save_transition(
            state.key,
            action.name,
            action.target,
            session_id,
            transition_log_level=automaton.get_state(action.target).transition_log_level,
            signal_values=signal_values,
            message_id=message_id,
            origin=origin,
            output_values=output_values,
        )
        AI_MEMORY_STRATEGIES[automaton.get_state(action.target).ai_memory_strategy].on_entry(self._env)
        return tracking_id

    def apply_action_env(
        self,
        automaton: Automaton,
        action: Action,
        signal_values: dict | None,
        selection: ChoiceSelection,
        state_key: str,
        *,
        username: str | None = None,
        project_id: str | None = None,
        session_id: int | None = None,
        output_values: dict | None = None,
    ) -> dict:
        """Applies `action`'s own env writes to the current scope — both
        the legacy declarative `env:` map and its own `on-exit` script
        (the future replacement for it, same `key = expr` writes, see
        Automaton.eval_action_on_exit) — shared by both the auto-tracking
        and manual-action paths (the latter fires with empty
        signal_values). Returns the keys it actually wrote, so whoever
        ran the turn can say so on the way out (see turn/outbound.py);
        on-exit's own value for a key wins over env:'s should an action
        somehow declare both. Then hands `action.task` (§6.5's
        task.* calls) to the scope's own task namespace, which runs it
        as an ActionTask due now — never inline here: task.prompt
        is a model call, send_mail a network call, and the browser gets
        whatever they produce over the websocket (see
        tracking/actuators/action_task.py). on-exit's own `chat.*` calls,
        by contrast, run synchronously right here — no ActionTask, no
        job queue — and are pushed over that exact same "notification"
        websocket frame via the scope's own chat namespace (see
        ChatNamespace.push_notification): chat.* has no server-side
        network/model call to keep off the event-loop thread, so there's
        nothing to hibernate. `session_id`: the firing session, for the
        ActionTask itself and for the chat namespace's own push.
        `output_values`: structured output dict from this turn's AI
        generation, available in env expressions and task/on-exit scripts."""
        if not action.env and not action.task and not action.on_exit:
            return {}
        scope = self._scope_builder.build(
            automaton, state_key, signal_values, selection, session_id=session_id, output_values=output_values,
        )
        updates: dict = {}
        if action.env:
            updates.update(automaton.eval_action_env(action, scope))
        chat_snippets: str | None = None
        if action.on_exit:
            on_exit_updates, chat_snippets = automaton.eval_action_on_exit(action, scope)
            updates.update(on_exit_updates)
        if updates:
            self._env.update_action_set(updates)
        if action.task:
            scope["task"].schedule_task(action, scope, session_id=session_id)
        if chat_snippets:
            scope["chat"].push_notification(chat_snippets)
        return updates

    def schedule_task(
        self, automaton: Automaton, action: Action, state_key: str, selection: ChoiceSelection,
        session_id: int | None = None,
    ) -> None:
        """`action.task` scheduled against a fresh scope, with no env
        applied — for a caller firing an action's task outside a
        real transition/env-apply path (a brand-new session's own
        init-action, a test-session reset), where env: is either
        irrelevant or already handled elsewhere."""
        if not action.task:
            return
        scope = self._scope_builder.build(automaton, state_key, None, selection, session_id=session_id)
        scope["task"].schedule_task(action, scope, session_id=session_id)
