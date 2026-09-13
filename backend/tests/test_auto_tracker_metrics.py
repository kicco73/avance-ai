"""Auto-tracking's metric-in-trigger support: a trigger expression can
reference a core metric (e.g. `engagement`) alongside/instead of a
declared signal, merged in only when referenced, never persisted onto
the Tracking row.
"""
from __future__ import annotations

import json

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from metrics.metric_service import MetricService
from system.web_session import WebSession
from tracking.fixed_project_context import FixedProjectContext
from turn_harness import PROJECT_ID, turn_service_for  # noqa: F401 — a pytest fixture, used by name

# Each test verifies one fact about metric-in-trigger evaluation:
# fires/doesn't fire, never leaks into the persisted Tracking row,
# computation is skipped when unreferenced.
pytestmark = pytest.mark.regression


def _automaton_with_trigger(trigger_expr: str, target: str = "b") -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target=target, trigger=trigger_expr)
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="bye", actions=[])
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
        # A real declared signal — signal_values are coerced against
        # exactly this list, dropping anything not declared here.
        signals=[Signal(name="mySignal", ui_label="My signal", definition="whatever")],
        general_attachments={},
        autotracking_on_ai_message=True,
        project_id=PROJECT_ID,
    )


class FakeSchemaAiService:
    """A v2 (schema)-shaped fake — reports `signals` straight through
    on_metadata as a raw JSON string."""

    def __init__(self, signals_json: str) -> None:
        self._signals_json = signals_json

    def get_models_info(self) -> dict:
        return {"auto": True, "current_index": 0, "models": []}

    def select_model(self, index: int | None) -> None:
        pass

    def is_provider_with_schema(self) -> bool:
        return True

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        # Only when actually asked for — a schema-constrained provider can't
        # emit a field outside the schema it was given, and a turn whose
        # triggers reference no signal never requests one.
        if "signals" in schema:
            on_metadata("signals", self._signals_json)
        yield "Hi!"


async def _talking_in(turn_service_for, automaton: Automaton, signals_json: str = '{"mySignal": 1}'):
    """A turn service over `automaton`, and a live session to talk in —
    a freshly entered session already scores "engagement" above zero via
    its session component alone, enough to drive these triggers."""
    turn_service = turn_service_for(automaton, ai_service=FakeSchemaAiService(signals_json))
    db = turn_service_for.db
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    return turn_service, session["id"]


async def test_a_trigger_referencing_only_a_metric_can_fire(turn_service_for):
    turn_service, session_id = await _talking_in(turn_service_for, _automaton_with_trigger("engagement >= 1"))

    result = await turn_service.process_turn(session_id, "hello")

    assert result["state_changed"] is True
    assert result["new_state"] == "b"


async def test_a_metric_referencing_trigger_that_is_not_met_does_not_fire(turn_service_for):
    turn_service, session_id = await _talking_in(turn_service_for, _automaton_with_trigger("engagement >= 99"))

    result = await turn_service.process_turn(session_id, "hello")

    assert result["state_changed"] is False
    assert result["new_state"] is None


async def test_metric_values_used_for_evaluation_are_never_persisted(turn_service_for):
    # mySignal must appear in the trigger too, not just engagement — a
    # signal no trigger references is dropped before persisting, same as
    # a metric, so an engagement-only trigger would filter it out too.
    turn_service, session_id = await _talking_in(
        turn_service_for, _automaton_with_trigger("signal.mySignal >= 1 and engagement >= 1"), '{"mySignal": 42}',
    )

    await turn_service.process_turn(session_id, "hello")

    persisted = [row for row in turn_service.get_session_signals(session_id) if row["values"]]
    assert len(persisted) == 1
    # Only the real, model-reported signal is stored — "engagement" (or
    # any metric) must never leak into the Tracking log.
    assert json.loads(persisted[0]["values"]) == {"mySignal": 42}


def test_metric_values_are_merged_into_the_evaluation_names_only_when_a_trigger_references_one(db):
    # The gate itself, where it lives: a full history load is what
    # computing metrics costs, and a state whose triggers name none of
    # them must not pay it.
    metrics = MetricService(db, FixedProjectContext(project_id=PROJECT_ID))
    names = {"mySignal": 42}

    unreferenced = metrics.merge_if_referenced(_automaton_with_trigger("signal.mySignal >= 1"), "a", names)
    referenced = metrics.merge_if_referenced(_automaton_with_trigger("engagement >= 1"), "a", names)

    assert unreferenced == names
    assert referenced["mySignal"] == 42
    assert "engagement" in referenced


async def test_a_trigger_can_combine_a_signal_and_a_metric(turn_service_for):
    turn_service, session_id = await _talking_in(
        turn_service_for, _automaton_with_trigger("signal.mySignal >= 40 and engagement >= 1"), '{"mySignal": 42}',
    )

    result = await turn_service.process_turn(session_id, "hello")

    assert result["state_changed"] is True
    assert result["new_state"] == "b"


async def test_a_trigger_referencing_only_env_can_fire(turn_service_for):
    """Mirror of the metric-only case for the other signal-less namespace:
    no signal is requested from the model (nothing in the trigger needs
    one), the trigger is still evaluated every turn against the empty
    signals set — the gate only ever switches off the request.

    The action-set store is seeded straight through the db: it is what an
    action's own `env:` field writes, and no service-level writer for it
    exists (set_env_value writes memory, which `env.` never reads)."""
    turn_service, session_id = await _talking_in(turn_service_for, _automaton_with_trigger("env.ready == 'yes'"))
    turn_service_for.db.set_action_env(session_id, {"ready": "yes"})

    result = await turn_service.process_turn(session_id, "hello")

    assert result["state_changed"] is True
    assert result["new_state"] == "b"


async def test_a_trigger_referencing_only_env_is_evaluated_before_the_reply_too(turn_service_for):
    """Same, under the "before" strategy (signal-tracking-on-ai-message:
    false): evaluated upfront, the optimistic reply in the old state is
    skipped and the one reply generated is already the new state's."""
    automaton = _automaton_with_trigger("env.ready == 'yes'")
    automaton.autotracking_on_ai_message = False
    turn_service, session_id = await _talking_in(turn_service_for, automaton)
    turn_service_for.db.set_action_env(session_id, {"ready": "yes"})

    result = await turn_service.process_turn(session_id, "hello")

    assert result["state_changed"] is True
    assert result["new_state"] == "b"
    assert turn_service.get_session_signals(session_id)[-1]["new_state"] == "b"


async def test_a_signal_less_evaluation_that_fires_nothing_leaves_no_snapshot_row(turn_service_for):
    turn_service, session_id = await _talking_in(turn_service_for, _automaton_with_trigger("env.ready == 'yes'"))
    turn_service_for.db.set_action_env(session_id, {"ready": "no"})

    result = await turn_service.process_turn(session_id, "hello")

    assert result["state_changed"] is False
    assert turn_service.get_session_signals(session_id) == []
