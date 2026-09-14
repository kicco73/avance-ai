"""TriggerExpressionAnalyzer.signal_domain_violations — static (build-time)
checking of what a trigger compares a `signal.*` against. A signal value is
an integer between Signal.MIN_VALUE and Signal.MAX_VALUE, so `signal.mood >
150` or `signal.mood == "alto"` has one fixed outcome no author intends.
"""
from __future__ import annotations

import pytest

from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("expression", [
    "signal.mood >= 75",
    "signal.mood == 0",
    "signal.mood <= 100",
    "signal.mood >= 0.5",
    "signal.mood >= -0",
    "0 <= signal.mood <= 100",
    "signal.mood >= env.threshold",
    "signal.mood > signal.energy",
    "signal.mood >= session.number_of_user_sessions",
    "env.stage == 'alto'",
    "user.name == 'ada'",
], ids=[
    "in-range", "lower-bound", "upper-bound", "fraction", "negative-zero", "chained-in-range",
    "dynamic-env", "two-signals", "session-field", "env-string", "user-string",
])
def test_no_violation_when_a_signal_is_matched_against_a_value_it_can_actually_take(expression):
    assert TriggerExpressionAnalyzer.signal_domain_violations(expression) == []


@pytest.mark.parametrize(("expression", "count", "mentions"), [
    ("signal.mood > 150", 1, ["signal.mood", "150", "between 0 and 100"]),
    ("150 < signal.mood", 1, ["signal.mood", "150"]),
    ("signal.mood >= -1", 1, ["-1"]),
    ("signal.mood == 'alto'", 1, ["signal.mood", "'alto'"]),
    ("signal.mood != True", 1, ["True"]),
    ("signal.mood == None", 1, ["None"]),
    ("0 <= signal.mood <= 200", 1, ["200"]),
    ("signal.mood > 150 and signal.energy == 'alta'", 2, []),
], ids=[
    "above-range", "reversed-operands", "below-range", "string", "bool", "none",
    "chained-comparison", "two-violations",
])
def test_a_signal_matched_against_a_value_outside_its_domain_is_flagged_once_per_offending_leg(
    expression, count, mentions
):
    violations = TriggerExpressionAnalyzer.signal_domain_violations(expression)

    assert len(violations) == count
    for mention in mentions:
        assert mention in violations[0]
