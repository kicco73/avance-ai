"""automaton.automaton.trigger_automaton_project_refs — every project
name referenced as `automaton.<project>...` in a trigger/env expression.
Used by automaton_builder.py's own self-loop-only build-time check and
by project_service.py's own reverse-index build.
"""
from __future__ import annotations

import pytest

from automaton.automaton import trigger_automaton_project_refs

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
