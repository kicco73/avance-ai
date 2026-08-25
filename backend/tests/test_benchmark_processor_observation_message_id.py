from __future__ import annotations

import pytest

from metrics.benchmark_processor import BenchmarkProcessor

pytestmark = pytest.mark.contract


class _FakeAutomaton:
    def __init__(self, autotracking_on_ai_message):
        self.autotracking_on_ai_message = autotracking_on_ai_message

    def get_state(self, key):
        return key


class _FakeTrackingEngine:
    def __init__(self):
        self.apply_transition_message_ids = []

    def evaluate_triggered_action(self, automaton, state, signal_values):
        return None

    def apply_transition(self, automaton, state, action, signal_values, session_id, message_id=None):
        self.apply_transition_message_ids.append(message_id)


class _FakeEnv:
    def update(self, stored_env):
        pass


class _FakeSessionFacts:
    def set_replay_instant(self, timestamp):
        pass

    def set_last_transition_instant(self, timestamp):
        pass


class _FakeMetrics:
    def advance_to(self, message_id, timestamp):
        pass


class _FakeSignalSource:
    async def get_turn_data(self, message_id, current_state):
        return {}, {}


class _FakeDb:
    def __init__(self, messages):
        self._messages = messages

    def get_chat_session(self, session_id):
        return {"id": session_id, "start_state": "start"}

    def get_messages(self, session_id):
        return self._messages

    def get_signals(self, session_id):
        return []


def _messages():
    return [
        {"id": 1, "role": "user", "timestamp": "2024-01-01T00:00:00"},
        {"id": 2, "role": "assistant", "timestamp": "2024-01-01T00:00:01"},
    ]


def _processor(autotracking_on_ai_message, engine):
    return BenchmarkProcessor(
        _FakeDb(_messages()), _FakeAutomaton(autotracking_on_ai_message), engine, _FakeEnv(),
        _FakeSessionFacts(), _FakeMetrics(), _FakeSignalSource(), sink=None,
    )


async def test_observation_message_id_is_the_user_message_when_autotracking_on_ai_message_is_false():
    engine = _FakeTrackingEngine()
    processor = _processor(False, engine)

    user_message_ids, warning = processor.prepare(1)
    assert warning is None
    for message_id in user_message_ids:
        await processor.process_message(1, message_id)

    assert engine.apply_transition_message_ids == [1]


async def test_observation_message_id_is_the_next_assistant_message_when_autotracking_on_ai_message_is_true():
    engine = _FakeTrackingEngine()
    processor = _processor(True, engine)

    user_message_ids, warning = processor.prepare(1)
    assert warning is None
    for message_id in user_message_ids:
        await processor.process_message(1, message_id)

    assert engine.apply_transition_message_ids == [2]
