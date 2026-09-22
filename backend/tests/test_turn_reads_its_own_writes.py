"""What a turn reads of itself, pinned before persistence moves to the end.

TurnTransaction (turn/turn_transaction.py) is the DB as the turn sees it.
Today it forwards every call to the Db at once; the next step keeps the
turn's rows in memory until the exchange commits. These tests pin, through
public entry points only, the four things that step must keep true:

- the history handed to the model ends with the messages being answered —
  a batch as one user message of several blocks, a lone message as a plain
  string — and, when the AI opens the conversation, with the `"..."` user
  turn some providers demand;
- the reply is bound to the messages it answered (`answered_by`), and the
  tokens the model reported land on the last of them;
- a metric evaluated in a trigger counts the message being answered, in
  both autotracking paths.
"""
from __future__ import annotations

import inspect

import pytest

from automaton.automaton import Action, Automaton, State
from metrics.metrics_framework.metrics.engagement import EngagementMetric
from metrics.metrics_framework.normalization import Normalizer
from system.web_session import WebSession
from turn_harness import PROJECT_ID, one_state_automaton, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.regression


class _RecordingProvider:
    def __init__(self, input_tokens: int = 0) -> None:
        self.histories: list[list[dict]] = []
        self._input_tokens = input_tokens

    async def generate_stream_with_schema(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        self.histories.append([dict(m) for m in history])
        if on_metadata is not None:
            on_metadata("input_tokens", self._input_tokens)
            on_metadata("output_tokens", 3)
        yield '{"text": "ok"}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0


async def _session(turn_service_for, automaton, provider):
    turn_service = turn_service_for(automaton, provider)
    db = turn_service_for.db
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    return turn_service, db, session["id"]


async def test_a_batch_reaches_the_model_as_one_user_message_of_several_blocks(turn_service_for):
    provider = _RecordingProvider()
    turn_service, _, session_id = await _session(
        turn_service_for, one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    first = turn_service.accept_user_message(session_id, "I have a problem")
    second = turn_service.accept_user_message(session_id, "with flight VY3003")

    await turn_service.process_turn(session_id, user_messages=[first, second])

    assert provider.histories[-1][-1] == {"role": "user", "content": ["I have a problem", "with flight VY3003"]}


async def test_a_lone_message_reaches_the_model_as_a_plain_string(turn_service_for):
    provider = _RecordingProvider()
    turn_service, _, session_id = await _session(
        turn_service_for, one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )

    await turn_service.process_turn(session_id, "hello")

    assert provider.histories[-1][-1] == {"role": "user", "content": "hello"}


async def test_a_conversation_the_ai_opens_is_asked_for_with_the_placeholder_user_turn(turn_service_for):
    provider = _RecordingProvider()
    turn_service, _, session_id = await _session(
        turn_service_for, one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )

    await turn_service.open_conversation(session_id)

    assert provider.histories[-1][-1] == {"role": "user", "content": "..."}
    assert [m["role"] for m in provider.histories[-1]].count("user") == 1


async def test_the_reply_answers_every_message_of_its_batch_and_the_tokens_land_on_the_last(turn_service_for):
    provider = _RecordingProvider(input_tokens=12)
    turn_service, db, session_id = await _session(
        turn_service_for, one_state_automaton(with_sources=False, autotracking_on_ai_message=False), provider,
    )
    first = turn_service.accept_user_message(session_id, "one")
    second = turn_service.accept_user_message(session_id, "two")

    result = await turn_service.process_turn(session_id, user_messages=[first, second])

    history = db.get_turn_history(session_id, None, None)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == ["one", "two"]
    assert history[0]["answered_by"] == result["assistant_message_id"]
    assert result["user_message_id"] == second.id
    assert db.get_message(second.id)["tokens"] == 12
    assert db.get_message(first.id)["tokens"] is None
    assert [m["content"] for m in turn_service.read_history(session_id)] == ["one", "two", "ok"]


def _automaton_writing_engagement(autotracking_on_ai_message: bool) -> Automaton:
    fires = Action(
        name="note", ui_label="Note", ui_button="", target="a",
        trigger="True", env={"engagement_at_trigger": "session.metric.engagement()"},
    )
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]),
        "a": State(input_processor="ai", key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[fires]),
    }
    return Automaton(
        init_action=init_action, states=states, general_prompt="", signals=[], general_attachments=(),
        autotracking_on_ai_message=autotracking_on_ai_message, sources=[], project_id=PROJECT_ID,
    )


def _engagement_of(user_messages: int, sessions: int) -> float:
    defaults = inspect.signature(EngagementMetric).parameters
    message_score = Normalizer.ratio(user_messages, defaults["message_reference"].default)
    session_score = Normalizer.ratio(sessions, defaults["session_reference"].default)
    return message_score * 0.6 + session_score * 0.4


@pytest.mark.parametrize("autotracking_on_ai_message", [False, True])
async def test_a_metric_evaluated_in_a_trigger_counts_the_message_being_answered(
    turn_service_for, autotracking_on_ai_message,
):
    turn_service, _, session_id = await _session(
        turn_service_for, _automaton_writing_engagement(autotracking_on_ai_message), _RecordingProvider(),
    )

    await turn_service.process_turn(session_id, "hello")

    written = turn_service.get_env(session_id)["action_set"]["engagement_at_trigger"]
    assert written == pytest.approx(_engagement_of(user_messages=1, sessions=1))
    assert written != pytest.approx(_engagement_of(user_messages=0, sessions=1))
