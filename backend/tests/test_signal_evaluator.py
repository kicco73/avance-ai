"""Tests for signals.evaluator.SignalEvaluator — the shared logic
behind both of AutoTracker's ways of getting signal values: `validate`
(embedded — a reply already generated for some other reason already
reported them) and `compute_explicitly` (no reply to piggyback on, makes
its own dedicated call using the exact same prompt/tag convention).
"""
from __future__ import annotations

from datetime import datetime

from automaton.automaton import Action, Automaton, Signal, State
from chat.env import Env
from chat.metadata_handler import MetadataHandler
from signals.evaluator import SignalEvaluator
from signals.definitions import Signals

USERNAME = "user"
PROJECT_NAME = "proj"


def _automaton(signals=None) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    state_a = State(key="a", ui_label="A", final=True, contextual_prompt="hi")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="",
        signals=signals or [],
        attachments={},
        general_attachments={},
        autotracking_on_user_message=False,
        autotracking_on_ai_message=False,
    )


def _evaluator() -> SignalEvaluator:
    return SignalEvaluator(MetadataHandler())


class FakeAiService:
    def __init__(self, reply: str | None = None, error: Exception | None = None) -> None:
        self._reply = reply
        self._error = error

    async def generate(self, system_prompt, history, on_retry=None) -> str:
        if self._error is not None:
            raise self._error
        return self._reply


def test_validate_coerces_a_valid_numeric_value():
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    result = _evaluator().validate(automaton, {"mood": 42})
    assert result == {"mood": 42}


def test_validate_turns_a_non_numeric_value_into_none():
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    result = _evaluator().validate(automaton, {"mood": "high"})
    assert result == {"mood": None}


def test_validate_turns_a_boolean_into_none():
    """bool is a subclass of int in Python — explicitly excluded."""
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    result = _evaluator().validate(automaton, {"mood": True})
    assert result == {"mood": None}


def test_validate_fills_in_none_for_a_missing_declared_signal():
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    result = _evaluator().validate(automaton, {})
    assert result == {"mood": None}


def test_validate_drops_anything_not_a_declared_signal():
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    result = _evaluator().validate(automaton, {"mood": 1, "somethingElse": 99})
    assert result == {"mood": 1}


def test_validate_of_none_raw_values_fills_every_declared_signal_with_none():
    automaton = _automaton([Signal(name="a", ui_label="A", definition="d"), Signal(name="b", ui_label="B", definition="d")])
    assert _evaluator().validate(automaton, None) == {"a": None, "b": None}


def test_validate_with_names_restricts_the_result_to_that_subset():
    automaton = _automaton([Signal(name="a", ui_label="A", definition="d"), Signal(name="b", ui_label="B", definition="d")])
    result = _evaluator().validate(automaton, {"a": 1, "b": 2}, names={"a"})
    assert result == {"a": 1}


def test_validate_with_names_still_fills_none_for_a_missing_needed_signal():
    automaton = _automaton([Signal(name="a", ui_label="A", definition="d"), Signal(name="b", ui_label="B", definition="d")])
    result = _evaluator().validate(automaton, {}, names={"a"})
    assert result == {"a": None}


def test_validate_with_an_empty_names_set_returns_nothing():
    automaton = _automaton([Signal(name="a", ui_label="A", definition="d")])
    result = _evaluator().validate(automaton, {"a": 1}, names=set())
    assert result == {}


async def test_compute_explicitly_extracts_and_validates_from_an_avance_tag(db):
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    signals = Signals(get_active_automaton=lambda: automaton, db=db)
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    session_id = db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    ai_service = FakeAiService(reply='Hi![avance]{"signals": {"mood": 75}}[/avance]')

    result = await _evaluator().compute_explicitly(
        ai_service, signals, env, build_priming_messages=lambda attachments: [], session_id=session_id
    )

    assert result == {"mood": 75}


async def test_compute_explicitly_degrades_to_none_values_on_ai_failure(db):
    from ai.llm_provider import AIServiceError

    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    signals = Signals(get_active_automaton=lambda: automaton, db=db)
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    session_id = db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    ai_service = FakeAiService(error=AIServiceError("boom"))

    result = await _evaluator().compute_explicitly(
        ai_service, signals, env, build_priming_messages=lambda attachments: [], session_id=session_id
    )

    assert result == {"mood": None}


async def test_compute_explicitly_with_names_only_prompts_for_that_subset(db):
    automaton = _automaton([
        Signal(name="mood", ui_label="Mood", definition="mood definition"),
        Signal(name="irrelevant", ui_label="Irrelevant", definition="irrelevant definition"),
    ])
    signals = Signals(get_active_automaton=lambda: automaton, db=db)
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    session_id = db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    captured = {}

    class CapturingAiService:
        async def generate(self, system_prompt, history, on_retry=None) -> str:
            captured["system_prompt"] = system_prompt
            return 'Hi![avance]{"signals": {"mood": 75, "irrelevant": 1}}[/avance]'

    result = await _evaluator().compute_explicitly(
        CapturingAiService(), signals, env, build_priming_messages=lambda attachments: [],
        session_id=session_id, names={"mood"},
    )

    assert "mood definition" in captured["system_prompt"]
    assert "irrelevant definition" not in captured["system_prompt"]
    # Even though the model reported "irrelevant" too (an over-eager
    # reply, or one from before this optimization) — it's outside
    # `names`, so validate() drops it rather than persisting it.
    assert result == {"mood": 75}


async def test_compute_explicitly_with_no_avance_tag_at_all_is_all_none(db):
    """A malformed/tagless reply must never crash — same graceful
    degradation as an AI-service failure."""
    automaton = _automaton([Signal(name="mood", ui_label="Mood", definition="d")])
    signals = Signals(get_active_automaton=lambda: automaton, db=db)
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    session_id = db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    ai_service = FakeAiService(reply="just a plain reply, no tags")

    result = await _evaluator().compute_explicitly(
        ai_service, signals, env, build_priming_messages=lambda attachments: [], session_id=session_id
    )

    assert result == {"mood": None}
