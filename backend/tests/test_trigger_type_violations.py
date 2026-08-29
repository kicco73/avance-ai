"""TriggerExpressionAnalyzer.type_violations — static (build-time) type-checking of a
trigger expression's ordering comparisons (`<`/`<=`/`>`/`>=`). E.g.
`system.today() >= 5` compares a date string against a number, which
raises TypeError the instant it's actually evaluated.
"""
from __future__ import annotations

import pytest

from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer

pytestmark = pytest.mark.contract


def test_no_violation_for_a_signal_compared_to_a_number():
    assert TriggerExpressionAnalyzer.type_violations("signal.mood >= 75") == []


def test_no_violation_for_a_bool_compared_to_a_number():
    # bool is a real int subtype in Python — True >= 0.5 never raises.
    assert TriggerExpressionAnalyzer.type_violations("signal.flagged >= 0.5") == []


def test_flags_a_string_returning_proxy_compared_numerically():
    violations = TriggerExpressionAnalyzer.type_violations("system.today() >= 5")
    assert len(violations) == 1
    assert "system.today()" in violations[0]
    assert "string" in violations[0]
    assert "number" in violations[0]


def test_flags_regardless_of_operand_order():
    assert len(TriggerExpressionAnalyzer.type_violations("5 <= system.today()")) == 1


def test_flags_only_the_offending_leg_of_a_chained_comparison():
    violations = TriggerExpressionAnalyzer.type_violations("0 <= signal.mood < system.time()")
    assert len(violations) == 1
    assert "system.time()" in violations[0]


def test_two_string_typed_identifiers_may_be_compared():
    # Lexicographic string ordering is legal Python, not a type error.
    assert TriggerExpressionAnalyzer.type_violations("system.today() >= system.time()") == []


def test_equality_is_never_checked_regardless_of_type():
    # == and != never raise in Python, whatever the two types are.
    assert TriggerExpressionAnalyzer.type_violations("system.today() == 5") == []
    assert TriggerExpressionAnalyzer.type_violations("system.today() != signal.mood") == []


def test_no_violation_when_either_side_is_not_statically_typeable():
    # env.* is a free-form dynamic store (see Action.env's own
    # docstring) — never guessed at.
    assert TriggerExpressionAnalyzer.type_violations("env.visits >= 5") == []
    assert TriggerExpressionAnalyzer.type_violations("system.today() >= env.threshold") == []


def test_no_violation_for_two_unresolvable_operands():
    assert TriggerExpressionAnalyzer.type_violations("env.a >= env.b") == []

def test_session_metric_and_metric_namespaces_are_numeric():
    assert TriggerExpressionAnalyzer.type_violations("session.metric.engagement() >= 50") == []
    assert TriggerExpressionAnalyzer.type_violations("metric.retention() >= 50") == []


def test_flags_a_known_string_session_field_compared_numerically():
    violations = TriggerExpressionAnalyzer.type_violations("session.last_user_session_datetime >= 5")
    assert len(violations) == 1


def test_numeric_session_fields_are_not_flagged():
    assert TriggerExpressionAnalyzer.type_violations("session.current_session_duration_in_minutes >= 5") == []
    assert TriggerExpressionAnalyzer.type_violations("session.number_of_user_sessions >= 5") == []
    assert TriggerExpressionAnalyzer.type_violations("session.state_duration_in_minutes >= 5") == []


def test_combines_multiple_violations_in_one_expression():
    violations = TriggerExpressionAnalyzer.type_violations("system.today() >= 5 and system.time() <= 10")
    assert len(violations) == 2
