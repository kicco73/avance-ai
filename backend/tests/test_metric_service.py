from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from metrics.metric_service import MetricService
from tracking.fixed_project_context import FixedProjectContext
from metrics.metrics_framework import AnalyticsCalculator

pytestmark = pytest.mark.contract

# MetricService always evaluates in a one_session context — metric_names()
# stays the full reserved-name registry, but calculate_values()/
# merge_if_referenced only return the subset meaningful there.
SESSION_SCOPED_METRIC_NAMES = {
    m.name for m in AnalyticsCalculator.default_metrics() if "one_session" in m.scope
}


def _automaton_with_trigger(trigger_expr: str) -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a", trigger=trigger_expr)
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    states = {
        "": State(key="", ui_label="", final=False, actions=[init_action]),
        "a": State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action]),
    }
    return Automaton(
        init_action=init_action,
        states=states,
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


def _metrics(db) -> MetricService:
    return MetricService(db, FixedProjectContext(project_id="proj"))


def test_calculate_values_is_a_flat_name_to_value_mapping_matching_calculate_all(db):
    metrics = _metrics(db)

    values = metrics.calculate_values()

    assert set(values) == SESSION_SCOPED_METRIC_NAMES
    for value in values.values():
        assert 0.0 <= value <= 100.0
    assert values == {m["name"]: m["value"] for m in metrics.calculate_all()}


def test_merge_if_referenced_computes_metrics_only_when_a_trigger_mentions_one_never_mutating_the_input(db):
    names = {"mySignal": 60}

    untouched = _metrics(db).merge_if_referenced(_automaton_with_trigger("mySignal >= 50"), "a", names)
    assert untouched is names  # unchanged — metrics were never even computed

    merged = _metrics(db).merge_if_referenced(_automaton_with_trigger("engagement >= 50"), "a", names)
    assert merged["mySignal"] == 60
    assert set(merged) == {"mySignal"} | SESSION_SCOPED_METRIC_NAMES
    assert names == {"mySignal": 60}


def test_for_turn_returns_a_fresh_namespace_of_the_all_sessions_metrics_reusing_one_calculator_within_it(db):
    metrics = _metrics(db)
    namespace = metrics.for_turn()

    assert namespace.retention() is not None
    calculator_after_first_call = namespace._calculator
    assert namespace.activity_consistency() is not None
    assert namespace._calculator is calculator_after_first_call
    assert not hasattr(namespace, "engagement")

    assert metrics.for_turn() is not namespace
