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
    return MetricService(db, FixedProjectContext(project_name="proj"))


def test_calculate_values_returns_a_flat_name_to_value_mapping(db):
    values = _metrics(db).calculate_values()

    assert set(values) == SESSION_SCOPED_METRIC_NAMES
    for value in values.values():
        assert 0.0 <= value <= 100.0


def test_calculate_values_matches_calculate_all(db):
    metrics = _metrics(db)

    values = metrics.calculate_values()
    all_metrics = {m["name"]: m["value"] for m in metrics.calculate_all()}

    assert values == all_metrics


def test_merge_if_referenced_leaves_names_untouched_when_no_trigger_mentions_a_metric(db):
    automaton = _automaton_with_trigger("mySignal >= 50")
    names = {"mySignal": 60}

    result = _metrics(db).merge_if_referenced(automaton, "a", names)

    assert result is names  # unchanged — metrics were never even computed


def test_merge_if_referenced_adds_metric_values_when_a_trigger_mentions_one(db):
    automaton = _automaton_with_trigger("engagement >= 50")
    names = {"mySignal": 60}

    result = _metrics(db).merge_if_referenced(automaton, "a", names)

    assert result["mySignal"] == 60
    assert set(result) == {"mySignal"} | SESSION_SCOPED_METRIC_NAMES


def test_merge_if_referenced_does_not_mutate_the_original_names_dict(db):
    automaton = _automaton_with_trigger("engagement >= 50")
    names = {"mySignal": 60}

    _metrics(db).merge_if_referenced(automaton, "a", names)

    assert names == {"mySignal": 60}


def test_for_turn_exposes_only_the_all_sessions_per_user_metrics(db):
    namespace = _metrics(db).for_turn()

    assert namespace.retention() is not None
    assert namespace.activity_consistency() is not None
    assert not hasattr(namespace, "engagement")


def test_for_turn_reuses_the_same_calculator_across_metrics(db):
    namespace = _metrics(db).for_turn()

    namespace.retention()
    calculator_after_first_call = namespace._calculator
    namespace.activity_consistency()

    assert namespace._calculator is calculator_after_first_call


def test_for_turn_returns_a_fresh_namespace_every_call(db):
    metrics = _metrics(db)

    assert metrics.for_turn() is not metrics.for_turn()
