"""TriggerExpressionAnalyzer.automaton_project_refs finds every project name
referenced as `automaton.<project>...` in a trigger/env expression;
automaton_env_refs narrows that to the automaton.<project>.env.<key> references.
"""
from __future__ import annotations

import pytest

from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer

pytestmark = pytest.mark.contract


@pytest.mark.parametrize(("expression", "expected"), [
    ("signal.mood >= 50", set()),
    ("automaton.otherProject.state == 'x'", {"otherProject"}),
    ("automaton.otherProject.env.someKey >= 1", {"otherProject"}),
    ("automaton.a.env.k1 and automaton.b.state == 'x'", {"a", "b"}),
    ("automaton.a.state == 'x' or automaton.a.env.k1 >= 1", {"a"}),
    ("signal.mood >= 50 and automaton.otherProject.state == 'x'", {"otherProject"}),
], ids=["none", "state", "env-key", "two-projects", "same-project-twice", "mixed-namespaces"])
def test_automaton_project_refs_reports_each_distinct_project_referenced_however_it_is_reached(expression, expected):
    assert TriggerExpressionAnalyzer.automaton_project_refs(expression) == expected


@pytest.mark.parametrize(("expression", "expected"), [
    ("signal.mood >= 50", {}),
    ("automaton.otherProject.state == 'x'", {}),
    ("automaton.dep.env.k1 >= 1", {"dep": {"k1"}}),
    ("automaton.dep.env.k1 >= 1 and automaton.dep.env.k2 == 'x'", {"dep": {"k1", "k2"}}),
    ("automaton.a.env.k1 >= 1 and automaton.b.env.k2 == 'x'", {"a": {"k1"}, "b": {"k2"}}),
    ("automaton.dep.state == 'y' and automaton.dep.env.k1 >= 1", {"dep": {"k1"}}),
], ids=["none", "state-is-not-env", "one-key", "keys-grouped", "projects-separate", "mixed-state-and-env"])
def test_automaton_env_refs_reports_only_env_keys_grouped_by_their_own_project(expression, expected):
    assert TriggerExpressionAnalyzer.automaton_env_refs(expression) == expected
