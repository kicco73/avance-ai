"""The session-replay loop is identical regardless of signal source
(turn-by-turn or batch): TestProcessor only ever calls
signal_source.get_turn_data(message_id, current_state)."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from automaton.automaton import Automaton
from db import Db
from tracking.env import Env
from tracking.session_facts import SessionFacts
from tracking.tracking_engine import TestObservationSink, TrackingEngine
from testing.metrics_provider import TestMetricsProvider


class TestSignalSource(Protocol):
    # Real AI calls made so far — TestReplayJob watches this to know when
    # one of its own "steps" (one real AI call, not one turn replayed) is
    # done, since a batch call can silently cover several turns at once.
    calls_made: int

    async def get_turn_data(self, message_id: int, current_state: str) -> tuple[dict, dict]:
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
    ) -> None:
        self._db = db
        self._automaton = automaton
        self._tracking_engine = tracking_engine
        self._env = env
        self._session_facts = session_facts
        self._metrics = metrics
        self._signal_source = signal_source
        self._sink = sink
        self._current_state: str | None = None
        self._ordered_ids: list[int] = []
        self._by_id: dict[int, dict] = {}

    def prepare(self, session_id: int) -> tuple[list[int], str | None]:
        session = self._db.get_chat_session(session_id)
        if session is None:
            return [], f"session {session_id}: not found, skipped"

        current_state = self._determine_starting_state(session_id, session)
        if current_state is None:
            return [], f"session {session_id}: no known starting state, skipped"

        messages = self._db.get_messages(session_id)
        self._by_id = {m['id']: m for m in messages}
        self._ordered_ids = sorted(self._by_id.keys())
        self._current_state = current_state
        return [mid for mid in self._ordered_ids if self._by_id[mid]['role'] == 'user'], None

    async def process_message(self, session_id: int, message_id: int) -> None:
        real_timestamp = _parse_utc(self._by_id[message_id]['timestamp'])
        self._session_facts.set_replay_instant(real_timestamp)
        self._metrics.advance_to(message_id, real_timestamp)

        signal_values, stored_env = await self._signal_source.get_turn_data(message_id, self._current_state)
        self._env.update(stored_env)

        state = self._automaton.get_state(self._current_state)
        action = self._tracking_engine.evaluate_triggered_action(self._automaton, state, signal_values)

        if action is not None:
            self._session_facts.set_last_transition_instant(real_timestamp)

        observation_message_id = (
            self._next_assistant_message_id(self._ordered_ids, self._by_id, message_id)
            if self._automaton.autotracking_on_ai_message else message_id
        )
        self._tracking_engine.apply_transition(
            self._automaton, state, action, signal_values, session_id,
            message_id=observation_message_id,
        )

        if action is not None:
            self._current_state = action.target

    def _determine_starting_state(self, session_id: int, session: dict) -> str | None:
        for row in self._db.get_signals(session_id):
            if row['expected_state']:
                return row['expected_state']
        return session['start_state']

    @staticmethod
    def _next_assistant_message_id(ordered_ids: list[int], by_id: dict, user_message_id: int) -> int | None:
        index = ordered_ids.index(user_message_id)
        if index + 1 < len(ordered_ids):
            next_id = ordered_ids[index + 1]
            if by_id[next_id]['role'] == 'assistant':
                return next_id
        return None
