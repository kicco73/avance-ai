from __future__ import annotations

import json
from typing import Protocol

from automaton.automaton import Action, Automaton, State
from automaton.choice import ChoiceSelection
from db.models import TestObservation
from system.logging_factory import LoggerFactory
from tracking.env import Env
from tracking.evaluation_scope import EvaluationScopeBuilder
from turn.turn_transaction import RowHandle, TurnDbInterface, row_id

logger = LoggerFactory.get_logger(__name__)


class TrackingSink(Protocol):
    """Whatever TrackingEngine needs to persist a signal snapshot/state
    transition — production writes to the real Db (see DbTrackingSink),
    while a test-replay sink can satisfy this independently."""

    def save_signal_snapshot(
        self, values: dict, session_id: int, message_id: RowHandle | None = None, output_values: dict | None = None,
    ) -> RowHandle:
        ...

    def save_transition(
        self,
        old_state: str,
        action: str,
        new_state: str,
        session_id: int,
        transition_log_level: str,
        signal_values: dict | None = None,
        message_id: RowHandle | None = None,
        origin: str | None = None,
        output_values: dict | None = None,
    ) -> RowHandle:
        ...

    def clear_local_memory(self, session_id: int) -> None:
        ...


class DbTrackingSink:
    """TrackingSink backed by the real Db — production's own sink."""

    def __init__(self, db: TurnDbInterface) -> None:
        self._db = db

    def save_signal_snapshot(
        self, values: dict, session_id: int, message_id: RowHandle | None = None, output_values: dict | None = None,
    ) -> RowHandle:
        return self._db.save_signal_snapshot(values, session_id, message_id, output_values=output_values)

    def save_transition(
        self,
        old_state: str,
        action: str,
        new_state: str,
        session_id: int,
        transition_log_level: str,
        signal_values: dict | None = None,
        message_id: RowHandle | None = None,
        origin: str | None = None,
        output_values: dict | None = None,
    ) -> RowHandle:
        return self._db.save_transition(
            old_state, action, new_state, session_id,
            transition_log_level=transition_log_level,
            signal_values=signal_values,
            message_id=message_id,
            origin=origin,
            output_values=output_values,
        )

    def clear_local_memory(self, session_id: int) -> None:
        self._db.clear_local_memory(session_id)


class TestObservationSink:
    """TrackingSink for a test replay — writes to TestObservation, never
    to Tracking, so a replay can never be mistaken for (or overwrite)
    real production data."""

    def __init__(self, run_id: int) -> None:
        self._run_id = run_id

    def save_signal_snapshot(
        self, values: dict, session_id: int, message_id: RowHandle | None = None, output_values: dict | None = None,
    ) -> RowHandle:
        row = TestObservation.create(
            run=self._run_id, session=session_id, message=row_id(message_id), values=json.dumps(values),
        )
        return RowHandle(row.id)

    def save_transition(
        self,
        old_state: str,
        action: str,
        new_state: str,
        session_id: int,
        transition_log_level: str,
        signal_values: dict | None = None,
        message_id: RowHandle | None = None,
        origin: str | None = None,
        output_values: dict | None = None,
    ) -> RowHandle:
        row = TestObservation.create(
            run=self._run_id, session=session_id, message=row_id(message_id),
            old_state=old_state, action=action, new_state=new_state,
            values=json.dumps(signal_values) if signal_values is not None else None,
        )
        return RowHandle(row.id)

    def clear_local_memory(self, session_id: int) -> None:
        return None

    def replaying(self, row: RowHandle, tracking_id: int) -> None:
        TestObservation.update(tracking=tracking_id).where(TestObservation.id == row_id(row)).execute()


class TrackingEngine:
    """Trigger evaluation + transition/env application. No temporal ("as
    of when") concept passes through these methods — the injected
    `env`/`scope_builder` already behaves correctly for its own context."""

    def __init__(
        self, sink: TrackingSink, env: Env, scope_builder: EvaluationScopeBuilder,
    ) -> None:
        self._sink = sink
        self._env = env
        self._scope_builder = scope_builder

    def evaluate_triggered_action(
        self, automaton: Automaton, state: State, signal_values: dict, selection: ChoiceSelection,
        session_id: int | None = None, output_values: dict | None = None,
    ) -> Action | None:
        """None whenever `state` has nothing triggerable. Only decides
        which action fires from already-computed signals — never whether
        they get computed at all. `session_id`: see
        EvaluationScopeBuilder.build. `output_values`: structured output
        dict from this turn's AI generation, available in trigger expressions."""
        if not state.has_triggerable_actions:
            return None

        scope = self._scope_builder.build(
            automaton, state.key, signal_values, selection, session_id=session_id, output_values=output_values,
        )
        return automaton.evaluate_triggers_action(state.key, scope)

    def evaluate_choice(
        self, automaton: Automaton, state_key: str, selection: ChoiceSelection, session_id: int,
        signal_values: dict | None = None,
    ) -> Action | None:
        scope = self._scope_builder.build(automaton, state_key, signal_values, selection, session_id=session_id)
        return automaton.evaluate_triggers_action(state_key, scope)

    def apply_transition(
        self,
        automaton: Automaton,
        state: State,
        action: Action | None,
        signal_values: dict | None,
        selection: ChoiceSelection,
        session_id: int,
        message_id: RowHandle | None = None,
        *,
        origin: str,
        username: str | None = None,
        project_id: str | None = None,
        output_values: dict | None = None,
    ) -> tuple[RowHandle, dict]:
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
        message_id: RowHandle | None = None,
        *,
        origin: str,
        username: str | None = None,
        project_id: str | None = None,
        output_values: dict | None = None,
    ) -> RowHandle:
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
        """Applies `action`'s own `on-exit` env writes to the current
        scope (see Automaton.eval_action_on_exit) — shared by both the
        auto-tracking and manual-action paths (the latter fires with
        empty signal_values). Returns the keys it actually wrote, so
        whoever ran the turn can say so on the way out (see
        turn/outbound.py). A key whose expression raised is never in
        that return value, and is not a value silently lost either: it
        is pushed to the interface as a `chat.notify(...)` toast, the
        same "notification" frame chat.* calls already use — a failed
        write is not something a log line alone should hide. Then hands
        `action.task` (§6.5's
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
        generation, available in task/on-exit scripts."""
        if session_id is not None:
            self._sink.clear_local_memory(session_id)
        if not action.task and not action.on_exit:
            return {}
        scope = self._scope_builder.build(
            automaton, state_key, signal_values, selection, session_id=session_id, output_values=output_values,
        )
        updates: dict = {}
        failures: list[tuple[str, Exception]] = []
        chat_snippets: str | None = None
        if action.on_exit:
            on_exit_updates, chat_snippets, on_exit_failures = automaton.eval_action_on_exit(action, scope)
            updates.update(on_exit_updates)
            failures.extend(on_exit_failures)
        if failures:
            failure_snippet = scope["chat"].notify("Automation error", self._failure_body(action, failures))
            chat_snippets = "\n".join(snippet for snippet in (chat_snippets, failure_snippet) if snippet)
        if updates:
            self._env.update_action_set(updates)
        if action.task:
            scope["task"].schedule_task(action, scope, session_id=session_id)
        if chat_snippets:
            scope["chat"].push_notification(chat_snippets)
        return updates

    @staticmethod
    def _failure_body(action: Action, failures: list[tuple[str, Exception]]) -> str:
        lines = [f"- `{label}` — {type(exc).__name__}: {exc}" for label, exc in failures]
        return f"Action '{action.name}' did not write everything it should have:\n" + "\n".join(lines)

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
