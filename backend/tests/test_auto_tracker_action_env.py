"""Auto-tracking's own end of the action-level `env` feature: once a
trigger fires an action, that action's `env` field (see
automaton_builder.py's _build_action/Automaton.eval_action_env) is
evaluated and merged onto tracking.env.Env's persisted store — so the
very next prompt already sees the updated value, not last turn's.

Rewritten for this refactor: `tracking/auto_tracker.py`'s `AutoTracker`
class no longer exists at all (deleted — ground truth table row #5).
Replaced by TrackingProcessorAfterUserMessage/TrackingProcessorAfterAiMessage
(see tracking/tracking_processor.py's _apply_action_env/_move_automaton),
constructed by TrackingService.process(). These tests now drive the same
feature through TrackingService.process() directly, in
autotracking_on_ai_message mode (autotracking_on_user_message=False,
which is what tracking_service.py:193-196 actually consults for
processor selection) — a single, deterministic AI call per turn, the
closest current equivalent to AutoTracker.run's own single-shot
semantics.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from tracking.env import Env
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

USERNAME = "user"
PROJECT_NAME = "proj"

# Every test here verifies a specific, punctual fact about the action-
# level env feature (persisted on fire, untouched otherwise, self-
# referencing, scoped to this turn's own signal values) — still real
# current behavior, just driven through a different entry point now.
pytestmark = pytest.mark.regression


def _automaton_with_env(trigger_expr: str, action_env: dict | None, target: str = "b") -> Automaton:
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target=target,
        trigger=trigger_expr, env=action_env,
    )
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=target == "b", contextual_prompt="bye", actions=[])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": state_a,
        "b": state_b,
    }
    return Automaton(
        init_action=init_action,
        states=states,
        general_prompt="",
        signals=[Signal(name="mySignal", ui_label="My signal", definition="whatever")],
        attachments={},
        general_attachments={},
        autotracking_on_user_message=False,
        autotracking_on_ai_message=True,
    )


class FakeProjectService:
    """Stands in for project.project_service.ProjectService — just enough
    for TrackingService to run a real turn against a fixed, hand-built
    automaton (see _automaton_with_env above), no file/YAML involved."""

    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME


class FakeSchemaAiService:
    """A v2 (schema)-shaped fake — reports `signals` straight through
    on_metadata as a raw JSON string, the same wire shape
    ai.ai_service.AiService.generate_stream_with_metadata actually uses
    (see tracking/turn_protocol_using_schema.py), so this never depends
    on tracking.text_filter.ConcatTagFilter's own tag-scanning at all."""

    def __init__(self, signals_json: str) -> None:
        self._signals_json = signals_json

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index: int | None) -> None:
        pass

    def is_provider_with_schema(self) -> bool:
        return True

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        on_metadata("signals", self._signals_json)
        yield "Hi!"


def _tracking_service(db, automaton: Automaton, signals_json: str = '{"mySignal": 1}') -> TrackingService:
    ai_service = FakeSchemaAiService(signals_json)
    project_service = FakeProjectService(automaton)
    metrics = MetricService(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    # TrackingService.__init__ now takes project_service directly, not
    # get_active_automaton/get_username/get_active_project_name callables
    # (see tracking/tracking_service.py).
    return TrackingService(db, ai_service, project_service, metrics)


def _session_id(db) -> int:
    return db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )


def _env(db) -> Env:
    return Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)


async def test_a_fired_actions_env_is_persisted(db):
    automaton = _automaton_with_env("mySignal >= 1", {"reset_counter": "True"})
    session_id = _session_id(db)

    result = await _tracking_service(db, automaton, '{"mySignal": 1}').process(session_id, "hello")

    assert result["state_changed"] is True
    env = _env(db)
    # Lands in the action-set store (see Env.update_action_set) — the
    # Inspector Env tab's own "SET" section — never the model-reported
    # `stored()` one (its own "AI" section).
    assert env.action_set() == {"reset_counter": True}
    assert env.stored() == {}


async def test_env_is_not_touched_when_the_trigger_does_not_fire(db):
    automaton = _automaton_with_env("mySignal >= 99", {"reset_counter": "True"})
    session_id = _session_id(db)

    result = await _tracking_service(db, automaton, '{"mySignal": 1}').process(session_id, "hello")

    assert result["state_changed"] is False
    env = _env(db)
    assert env.get("reset_counter") is None


async def test_an_env_expression_can_self_reference_the_previous_stored_value(db):
    automaton = _automaton_with_env("mySignal >= 1", {"number_of_steps": "number_of_steps + 1"}, target="a")
    session_id = _session_id(db)
    env = _env(db)
    # Seeded directly in the action-set store (see Env.update_action_set)
    # — as a real int, since that's what simpleeval always produces,
    # unlike a value the model itself reported via [env], which is
    # always a plain string (see MetadataHandler.parse_raw_env).
    env.update_action_set({"number_of_steps": 3})

    result = await _tracking_service(db, automaton, '{"mySignal": 1}').process(session_id, "hello")

    assert result["state_changed"] is True  # a self-loop (target == "a") still counts as fired
    assert env.action_set()["number_of_steps"] == 4


async def test_self_referencing_an_env_key_that_was_never_stored_yet_leaves_it_unset(db):
    automaton = _automaton_with_env("mySignal >= 1", {"number_of_steps": "number_of_steps + 1"}, target="a")
    session_id = _session_id(db)

    await _tracking_service(db, automaton, '{"mySignal": 1}').process(session_id, "hello")

    env = _env(db)
    assert env.get("number_of_steps") is None


async def test_env_can_reference_a_signal_value_from_this_same_turn(db):
    automaton = _automaton_with_env("mySignal >= 1", {"last_signal": "mySignal"})
    session_id = _session_id(db)

    await _tracking_service(db, automaton, '{"mySignal": 7}').process(session_id, "hello")

    env = _env(db)
    assert env.get("last_signal") == 7


async def test_an_action_with_no_env_field_never_touches_the_action_set_store(db):
    # Rewritten: the old assertion monkeypatched AutoTracker's own
    # `_env.update` (the model-reported/[env] store) directly to prove it
    # was never called — that object no longer exists. The actual
    # current guarantee (tracking/tracking_processor.py:239-240's `if not
    # action.env: return`) is that a fired action with no `env:` field
    # never writes to the action-set store either, which is what still
    # matters here and is what's asserted directly now.
    automaton = _automaton_with_env("mySignal >= 1", None)
    session_id = _session_id(db)

    result = await _tracking_service(db, automaton, '{"mySignal": 1}').process(session_id, "hello")

    assert result["state_changed"] is True
    env = _env(db)
    assert env.action_set() == {}
    assert env.stored() == {}
