from __future__ import annotations

import logging

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract

TRIGGERS = {
    "or_flag": "(signal.a + signal.b) / 2 <= 20 or env.flag",
    "average": "(signal.a + signal.b) / 2 <= 20",
    "not_equal": "signal.a != 50",
    "negated": "not signal.a",
    "bare": "signal.a",
    "and_or": "(env.flag and signal.a > 10) or env.n == 3",
    "no_signals": "env.n == 3",
    "malformed": "env.n / 0 > 1",
}


def _yml() -> str:
    states = "".join(
        f"  {key}:\n"
        "    input-processor: ai\n"
        "    contextual-prompt: hi\n"
        "    actions:\n"
        "      - name: fire\n"
        f"        target: {key}\n"
        f"        trigger: \"{trigger}\"\n"
        for key, trigger in TRIGGERS.items()
    )
    return (
        "project:\n  id: proj\n"
        "init-action:\n  target: or_flag\n"
        "signals:\n"
        "  a:\n    definition: a\n"
        "  b:\n    definition: b\n"
        "env:\n"
        "  flag:\n    type: bool\n"
        "  n:\n    type: number\n"
        f"states:\n{states}"
    )


@pytest.fixture(scope="module")
def automaton():
    return AutomatonBuilder().build({"index.yml": _yml()})


def _fires(automaton, state_key: str, signals: dict, flag: bool = False, n: int = 0) -> bool:
    scope = {"signal": {"a": None, "b": None, **signals}, "env": {"flag": flag, "n": n}}
    return automaton.evaluate_triggers_action(state_key, scope) is not None


@pytest.mark.parametrize(("flag", "fires"), [(True, True), (False, False)])
def test_a_missing_signal_leaves_the_rest_of_the_expression_counting(automaton, flag, fires):
    assert _fires(automaton, "or_flag", {}, flag=flag) is fires


@pytest.mark.parametrize(("state_key", "signals", "fires"), [
    ("or_flag", {"a": 10, "b": 20}, True),
    ("or_flag", {"a": 50, "b": 50}, False),
    ("average", {"a": 10, "b": 20}, True),
    ("not_equal", {"a": 40}, True),
    ("negated", {"a": 0}, True),
    ("bare", {"a": 7}, True),
])
def test_present_signals_evaluate_as_before(automaton, state_key, signals, fires):
    assert _fires(automaton, state_key, signals) is fires


@pytest.mark.parametrize("state_key", ["average", "not_equal", "bare"])
def test_every_use_of_a_missing_signal_is_false_without_a_warning(automaton, state_key, caplog):
    with caplog.at_level(logging.WARNING):
        assert _fires(automaton, state_key, {"b": 5}) is False
    assert not caplog.records


def test_not_works_as_usual_on_a_missing_signal_used_as_a_boolean(automaton):
    assert _fires(automaton, "negated", {}) is True


@pytest.mark.parametrize(("flag", "n", "fires"), [(True, 0, False), (True, 3, True), (False, 3, True)])
def test_boolean_operators_combine_a_missing_comparison_as_false(automaton, flag, n, fires):
    assert _fires(automaton, "and_or", {}, flag=flag, n=n) is fires


def test_a_trigger_with_no_signals_is_unchanged(automaton):
    assert _fires(automaton, "no_signals", {}, n=3) is True
    assert _fires(automaton, "no_signals", {}, n=2) is False


def test_a_malformed_expression_is_false_with_a_warning(automaton, caplog):
    with caplog.at_level(logging.WARNING):
        assert _fires(automaton, "malformed", {}, n=1) is False
    assert any("Trigger evaluation failed" in record.getMessage() for record in caplog.records)
