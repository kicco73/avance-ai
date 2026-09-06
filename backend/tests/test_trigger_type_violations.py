"""TriggerExpressionAnalyzer.type_violations — static (build-time) type-checking of a
trigger expression's ordering comparisons (`<`/`<=`/`>`/`>=`). E.g.
`user.name >= 5` compares a string against a number, which
raises TypeError the instant it's actually evaluated.
"""
from __future__ import annotations

import pytest

from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("expression", [
    "signal.mood >= 75",
    "signal.flagged >= 0.5",
    "user.name >= user.email",
    "user.name == 5",
    "user.name != signal.mood",
    "env.visits >= 5",
    "user.name >= env.threshold",
    "env.a >= env.b",
    "session.metric.engagement() >= 50",
    "metric.retention() >= 50",
    "session.current_session_duration_in_minutes >= 5",
    "session.number_of_user_sessions >= 5",
    "session.state_duration_in_minutes >= 5",
], ids=[
    "signal-vs-number", "bool-vs-number", "string-vs-string", "equality", "inequality",
    "dynamic-env", "string-vs-env", "two-dynamic", "session-metric", "metric", "duration",
    "session-count", "state-duration",
])
def test_no_violation_for_comparable_types_equality_or_operands_that_are_not_statically_typeable(expression):
    """bool is a real int subtype in Python — True >= 0.5 never raises;
    lexicographic string ordering is legal Python; == and != never raise
    whatever the two types are; env.* is a free-form dynamic store (see
    Action.env's own docstring), never guessed at."""
    assert TriggerExpressionAnalyzer.type_violations(expression) == []


@pytest.mark.parametrize(("expression", "count", "mentions"), [
    ("user.name >= 5", 1, ["user.name", "string", "number"]),
    ("5 <= user.name", 1, ["user.name"]),
    ("0 <= signal.mood < user.email", 1, ["user.email"]),
    ("session.last_user_session_datetime >= 5", 1, []),
    ("user.name >= 5 and user.email <= 10", 2, []),
], ids=["string-vs-number", "reversed-operands", "chained-comparison", "string-session-field", "two-violations"])
def test_a_string_typed_identifier_ordered_against_a_number_is_flagged_once_per_offending_leg(expression, count, mentions):
    violations = TriggerExpressionAnalyzer.type_violations(expression)

    assert len(violations) == count
    for mention in mentions:
        assert mention in violations[0]
