"""trigger_automaton_project_refs finds every project name referenced as
`automaton.<project>...` in a trigger/env expression; trigger_automaton_env_refs
narrows that to the automaton.<project>.env.<key> references.
"""
from __future__ import annotations

import pytest

from automaton.automaton import trigger_automaton_env_refs, trigger_automaton_project_refs

pytestmark = pytest.mark.contract


def test_no_automaton_reference_returns_an_empty_set():
    assert trigger_automaton_project_refs("signal.mood >= 50") == set()


def test_a_state_reference_is_found():
    assert trigger_automaton_project_refs("automaton.otherProject.state == 'x'") == {"otherProject"}


def test_an_env_key_reference_is_found():
    assert trigger_automaton_project_refs("automaton.otherProject.env.someKey >= 1") == {"otherProject"}


def test_multiple_distinct_projects_are_all_found():
    expr = "automaton.a.env.k1 and automaton.b.state == 'x'"
    assert trigger_automaton_project_refs(expr) == {"a", "b"}


def test_the_same_project_referenced_twice_counts_once():
    expr = "automaton.a.state == 'x' or automaton.a.env.k1 >= 1"
    assert trigger_automaton_project_refs(expr) == {"a"}


def test_a_reference_mixed_with_other_namespaces_only_reports_automaton():
    expr = "signal.mood >= 50 and automaton.otherProject.state == 'x'"
    assert trigger_automaton_project_refs(expr) == {"otherProject"}


class TestTriggerAutomatonEnvRefs:
    def test_no_reference_returns_an_empty_dict(self):
        assert trigger_automaton_env_refs("signal.mood >= 50") == {}

    def test_a_state_reference_is_not_an_env_reference(self):
        assert trigger_automaton_env_refs("automaton.otherProject.state == 'x'") == {}

    def test_a_single_env_reference_is_found(self):
        assert trigger_automaton_env_refs("automaton.dep.env.k1 >= 1") == {"dep": {"k1"}}

    def test_multiple_keys_on_the_same_project_are_grouped(self):
        expr = "automaton.dep.env.k1 >= 1 and automaton.dep.env.k2 == 'x'"
        assert trigger_automaton_env_refs(expr) == {"dep": {"k1", "k2"}}

    def test_keys_on_different_projects_are_kept_separate(self):
        expr = "automaton.a.env.k1 >= 1 and automaton.b.env.k2 == 'x'"
        assert trigger_automaton_env_refs(expr) == {"a": {"k1"}, "b": {"k2"}}

    def test_mixed_state_and_env_references_only_report_the_env_one(self):
        expr = "automaton.dep.state == 'y' and automaton.dep.env.k1 >= 1"
        assert trigger_automaton_env_refs(expr) == {"dep": {"k1"}}
