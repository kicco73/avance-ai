"""The session-replay loop is identical regardless of signal source
(turn-by-turn or batch): TestProcessor only ever calls
signal_source.get_turn_data(message_id, current_state)."""
from __future__ import annotations

from automaton.choice import ChoiceSelection

from datetime import datetime
from typing import Protocol

from automaton.automaton import Action, Automaton, State
from automaton.model import env_defaults_action
from db import Db
from tracking.env import Env
from tracking.session_facts import SessionFacts
from tracking.tracking_engine import TestObservationSink, TrackingEngine
from testing.metrics_provider import TestMetricsProvider
from testing.replay_messages import next_assistant_message_id
from turn.sessions.session_type_strategy import get_session_type_strategy
from turn.turn_transaction import RowHandle


class TestSignalSource(Protocol):
    calls_made: int

    async def get_turn_data(self, message_id: int, current_state: str) -> tuple[dict, dict, dict]:
        ...


def _parse_utc(iso_timestamp: str | None) -> datetime | None:
    """Db dict methods return timestamps as timezone-aware ISO strings, but
    downstream code compares against naive-UTC datetimes, so the parsed
    timezone offset is stripped here to match."""
    if iso_timestamp is None:
        return None
    return datetime.fromisoformat(iso_timestamp).replace(tzinfo=None)


class TestProcessor(object):
    """Extends (tracking_engine, env, metrics, signal_source, sink) with
    `db`/`automaton`, needed to walk session messages and evaluate/apply
    against the automaton, and `session_facts` for replay/transition
    instants. One instance replays exactly one session, one message at a
    time: prepare() sets up the session and hands back its message ids,
    process_message() advances by exactly one — the caller (TestReplayJob)
    owns the loop, so it can yield to other jobs between messages."""

    def __init__(
        self,
        db: Db,
        automaton: Automaton,
        tracking_engine: TrackingEngine,
        env: Env,
        session_facts: SessionFacts,
        metrics: TestMetricsProvider,
        signal_source: TestSignalSource,
        sink: TestObservationSink,
        messages: list[dict],
    ) -> None:
        self._db = db
        self._automaton = automaton
        self._tracking_engine = tracking_engine
        self._env = env
        self._session_facts = session_facts
        self._metrics = metrics
        self._signal_source = signal_source
        self._sink = sink
        self._messages = messages
        self._current_state: str | None = None
        self._ordered_ids: list[int] = []
        self._by_id: dict[int, dict] = {}
        self._pending_actions: list[dict] = []
        self._last_signals: dict = {}

    def prepare(self, session_id: int) -> tuple[list[int], str | None]:
        session = self._db.get_chat_session(session_id)
        if session is None:
            return [], f"session {session_id}: not found, skipped"

        if self._fired_init_action(session):
            current_state = self._start_from_init_action(session_id)
        else:
            self._backfill_declared_env_keys(session_id)
            current_state = self._determine_starting_state(session_id, session)
        if current_state is None:
            return [], f"session {session_id}: no known starting state, skipped"

        self._by_id = {m['id']: m for m in self._messages}
        self._ordered_ids = sorted(self._by_id.keys())
        self._current_state = current_state
        self._pending_actions = sorted(
            (row for row in self._db.get_signals(session_id) if row['position'] is not None),
            key=lambda row: (row['position'], row['id']),
        )
        return [mid for mid in self._ordered_ids if self._by_id[mid]['role'] == 'user'], None

    async def process_message(self, session_id: int, message_id: int) -> None:
        self._fire_actions_up_to(session_id, self._ordered_ids.index(message_id))
        real_timestamp = _parse_utc(self._by_id[message_id]['timestamp'])
        self._session_facts.set_replay_instant(real_timestamp)
        self._metrics.advance_to(message_id, real_timestamp)

        signal_values, stored_memory, output_values = await self._signal_source.get_turn_data(
            message_id, self._current_state,
        )
        self._env.update(stored_memory, declared_keys=self._automaton.declared_env_key_names())

        state = self._automaton.get_state(self._current_state)
        scope = self._automaton.signals_in_scope(state.key, signal_values, self._last_signals)
        if signal_values and not self._automaton.read_signal_names(state.key):
            self._last_signals = signal_values
        action = self._tracking_engine.evaluate_triggered_action(
            self._automaton, state, scope, ChoiceSelection.NONE, output_values=output_values,
        )

        if action is not None:
            self._session_facts.set_last_transition_instant(real_timestamp)

        observation_message_id = (
            next_assistant_message_id(self._ordered_ids, self._by_id, message_id)
            if self._automaton.autotracking_on_ai_message else message_id
        )
        output_for_env = {name: value for name, value in output_values.items() if name in state.output}
        if output_for_env:
            self._env.update_action_set(output_for_env, origin="output")
        self._tracking_engine.apply_transition(
            self._automaton, state, action, signal_values, ChoiceSelection.NONE, session_id,
            message_id=RowHandle(observation_message_id),
            origin='trigger',
            output_values=output_values, scope_signals=scope,
        )

        if action is not None:
            self._current_state = action.target

    def finish(self, session_id: int) -> None:
        self._fire_actions_up_to(session_id, len(self._ordered_ids))

    def _fire_actions_up_to(self, session_id: int, position: int) -> None:
        while self._pending_actions and self._pending_actions[0]['position'] <= position:
            self._fire(session_id, self._pending_actions.pop(0))

    def _fire(self, session_id: int, entry: dict) -> None:
        real_timestamp = _parse_utc(entry['timestamp'])
        self._session_facts.set_replay_instant(real_timestamp)
        preceding = self._ordered_ids[:entry['position']]
        self._metrics.advance_to(preceding[-1] if preceding else 0, real_timestamp)
        state = self._automaton.get_state(self._current_state)
        selection = (
            ChoiceSelection(key=entry['choice']['key'], option=entry['choice']['option'])
            if entry['choice'] is not None else ChoiceSelection.NONE
        )
        action = self._available(state, entry, selection, session_id)
        if action is not None:
            self._session_facts.set_last_transition_instant(real_timestamp)
        row, _ = self._tracking_engine.apply_transition(
            self._automaton, state, action, self._last_signals, selection, session_id, origin='manual',
        )
        self._sink.replaying(row, entry['id'])
        if action is not None:
            self._current_state = action.target

    def _available(self, state: State, entry: dict, selection: ChoiceSelection, session_id: int) -> Action | None:
        if entry['choice'] is not None:
            if selection.key not in state.choice_keys:
                return None
            return self._tracking_engine.evaluate_choice(
                self._automaton, state.key, selection, session_id, self._last_signals,
            )
        try:
            return self._automaton.move(state.key, entry['action'])
        except ValueError:
            return None

    def _fired_init_action(self, session: dict) -> bool:
        earlier = self._db.list_chat_sessions(session['username'], session['project_id'], type=session['type'])
        ran_before = any(row['id'] < session['id'] for row in earlier)
        return get_session_type_strategy(session['type']).fired_init_action(self._automaton, ran_before)

    def _start_from_init_action(self, session_id: int) -> str:
        self._env.clear()
        self._backfill_declared_env_keys(session_id)
        self._tracking_engine.apply_transition(
            self._automaton, self._automaton.states[""], self._automaton.init_action, None, ChoiceSelection.NONE,
            session_id, origin='init-action',
        )
        return self._automaton.init_action.target

    def _backfill_declared_env_keys(self, session_id: int) -> None:
        current = self._env.action_set()
        missing = [env_key for env_key in self._automaton.env_keys if env_key.name not in current]
        if not missing:
            return
        self._tracking_engine.apply_action_env(
            self._automaton, env_defaults_action(missing), {}, ChoiceSelection.NONE, "", session_id=session_id,
        )

    def _determine_starting_state(self, session_id: int, session: dict) -> str | None:
        if session['start_state']:
            return session['start_state']
        for row in self._db.get_signals(session_id):
            if row['expected_state']:
                return row['expected_state']
        return None
