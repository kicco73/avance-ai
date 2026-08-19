"""The session-replay loop, identical regardless of which signal source
backs it (turn-by-turn or batch — see benchmark_signal_sources.py):
BenchmarkProcessor only ever calls signal_source.get_turn_data(message_id,
current_state), never anything strategy-specific of its own."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol

from automaton.automaton import Automaton
from db import Db
from tracking.env import Env
from tracking.session_facts import SessionFacts
from tracking.tracking_engine import BenchmarkRunObservationSink, TrackingEngine
from metrics.metric_service import BenchmarkMetricsProvider


class BenchmarkSignalSource(Protocol):
    async def get_turn_data(self, message_id: int, current_state: str) -> tuple[dict, dict]:
        ...


def _parse_utc(iso_timestamp: str | None) -> datetime | None:
    """Every Db dict method hands back timestamps as an _utc_iso() string
    (see db/utils.py) — set_replay_instant/advance_to need a real naive-
    UTC datetime instead, matching every other DateTimeField's own
    convention (see db/db.py's _utc_iso docstring), so the timezone
    offset fromisoformat parses back in is stripped again here rather
    than compared against naive datetimes elsewhere and silently
    mismatching."""
    if iso_timestamp is None:
        return None
    return datetime.fromisoformat(iso_timestamp).replace(tzinfo=None)


class BenchmarkProcessor:
    """Constructor extends the (tracking_engine, env, metrics,
    signal_source, sink) shape with `db`/`automaton` (the loop needs both
    directly — to walk the session's own messages and to evaluate/apply
    against the automaton, see run_session below) and `session_facts`
    (set_replay_instant/set_last_transition_instant live there, not on
    Env — see tracking/session_facts.py's own docstring; this postdates
    the tracking_engine.py refactor from TrackingEngine(sink, env,
    metrics) to TrackingEngine(sink, env, scope_builder))."""

    def __init__(
        self,
        db: Db,
        automaton: Automaton,
        tracking_engine: TrackingEngine,
        env: Env,
        session_facts: SessionFacts,
        metrics: BenchmarkMetricsProvider,
        signal_source: BenchmarkSignalSource,
        sink: BenchmarkRunObservationSink,
    ) -> None:
        self._db = db
        self._automaton = automaton
        self._tracking_engine = tracking_engine
        self._env = env
        self._session_facts = session_facts
        self._metrics = metrics
        self._signal_source = signal_source
        self._sink = sink

    async def run_session(self, session_id: int, run: dict, report_progress: Callable[[], None]) -> str | None:
        session = self._db.get_chat_session(session_id)
        if session is None:
            return f"session {session_id}: not found, skipped"

        current_state = self._determine_starting_state(session_id, session)
        if current_state is None:
            return f"session {session_id}: no known starting state, skipped"

        messages = self._db.get_messages(session_id)
        by_id = {m['id']: m for m in messages}
        ordered_ids = sorted(by_id.keys())
        user_message_ids = [mid for mid in ordered_ids if by_id[mid]['role'] == 'user']

        for message_id in user_message_ids:
            real_timestamp = _parse_utc(by_id[message_id]['timestamp'])
            self._session_facts.set_replay_instant(real_timestamp)
            self._metrics.advance_to(message_id, real_timestamp)

            signal_values, stored_env = await self._signal_source.get_turn_data(message_id, current_state)
            self._env.update(stored_env)

            state = self._automaton.get_state(current_state)
            action = self._tracking_engine.evaluate_triggered_action(self._automaton, state, signal_values)

            if action is not None:
                self._session_facts.set_last_transition_instant(real_timestamp)

            self._tracking_engine.apply_transition(
                self._automaton, state, action, signal_values, session_id,
                message_id=self._next_assistant_message_id(ordered_ids, by_id, message_id),
            )

            if action is not None:
                current_state = action.target

            report_progress()

        return None

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
