from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from metrics.metric_service import MetricService
from metrics.metrics_framework import AnalyticsCalculator

# Every test here verifies MetricService's own interface guarantees
# (calculate_values' shape, its consistency with calculate_all,
# merge_if_referenced's opt-in/no-mutation behavior) rather than a
# punctual numeric fact — uniformly contract.
pytestmark = pytest.mark.contract

# MetricService always evaluates in a one_session context (a chat turn
# only ever runs within one session) — metric_names() itself stays the
# *full* reserved-name registry (still needed for trigger-expression
# validation at project-load time), but calculate_values()/
# merge_if_referenced only ever return the subset actually meaningful
# there (see AnalyticsCalculator's own default-metric filtering).
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
        autotracking_on_user_message=True,
        autotracking_on_ai_message=False,
    )


def _metrics(db) -> MetricService:
    return MetricService(db, get_username=lambda: "user", get_active_project_name=lambda: "proj")


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
