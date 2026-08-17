from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from metrics.metrics_framework import metric_names

pytestmark = pytest.mark.contract


def test_a_signal_named_after_a_metric_is_rejected():
    reserved = sorted(metric_names())[0]
    content = f"""
init-action:
  target: a
signals:
  {reserved}:
    definition: "whatever"
states:
  a:
    contextual-prompt: "hi"
"""
    with pytest.raises(ValueError, match="reserved for core metrics"):
        AutomatonBuilder().build({"index.yml": content})


@pytest.mark.parametrize("metric", sorted(metric_names()))
def test_every_metric_name_individually_is_rejected_as_a_signal_name(metric):
    content = f"""
init-action:
  target: a
signals:
  {metric}:
    definition: "whatever"
states:
  a:
    contextual-prompt: "hi"
"""
    with pytest.raises(ValueError, match="reserved for core metrics"):
        AutomatonBuilder().build({"index.yml": content})


def test_signals_not_matching_any_metric_name_build_fine():
    content = """
init-action:
  target: a
signals:
  myOwnSignal:
    definition: "whatever"
states:
  a:
    contextual-prompt: "hi"
"""
    automaton = AutomatonBuilder().build({"index.yml": content})

    assert [s.name for s in automaton.signals] == ["myOwnSignal"]


def test_a_trigger_may_reference_a_metric_name_with_no_matching_signal_declared():
    metric = sorted(metric_names())[0]
    content = f"""
init-action:
  target: a
states:
  a:
    contextual-prompt: "hi"
    actions:
      - name: advance
        target: b
        trigger: "{metric} >= 50"
  b:
    contextual-prompt: "bye"
"""
    automaton = AutomatonBuilder().build({"index.yml": content})

    assert automaton.states["a"].actions[0].trigger == f"{metric} >= 50"
    assert automaton.triggers_reference("a", {metric}) is True


def test_a_trigger_may_combine_a_declared_signal_and_a_metric_name():
    metric = sorted(metric_names())[0]
    content = f"""
init-action:
  target: a
signals:
  myOwnSignal:
    definition: "whatever"
states:
  a:
    contextual-prompt: "hi"
    actions:
      - name: advance
        target: b
        trigger: "myOwnSignal >= 50 and {metric} >= 10"
  b:
    contextual-prompt: "bye"
"""
    automaton = AutomatonBuilder().build({"index.yml": content})

    assert automaton.states["a"].actions[0].trigger == f"myOwnSignal >= 50 and {metric} >= 10"


def test_a_trigger_referencing_a_truly_unknown_name_is_still_rejected():
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: "hi"
    actions:
      - name: advance
        target: b
        trigger: "totallyUnknownName >= 50"
  b:
    contextual-prompt: "bye"
"""
    with pytest.raises(ValueError, match="undefined signal"):
        AutomatonBuilder().build({"index.yml": content})
