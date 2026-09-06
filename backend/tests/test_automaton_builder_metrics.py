from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from metrics.metrics_framework import metric_names

pytestmark = pytest.mark.contract


def _build(signals_yaml: str = "", trigger: str | None = None):
    trigger_yaml = (
        f'    actions:\n      - name: advance\n        target: b\n        trigger: "{trigger}"\n  b:\n    contextual-prompt: "bye"\n'
        if trigger else ""
    )
    content = f"""
project:
  id: proj
{signals_yaml}init-action:
  target: a
states:
  a:
    contextual-prompt: "hi"
{trigger_yaml}"""
    return AutomatonBuilder().build({"index.yml": content})


def _signal(name: str) -> str:
    return f'signals:\n  {name}:\n    definition: "whatever"\n'


@pytest.mark.parametrize("metric", sorted(metric_names()))
def test_every_metric_name_is_reserved_and_rejected_as_a_signal_name(metric):
    with pytest.raises(ValueError, match="reserved for core metrics"):
        _build(_signal(metric))


def test_a_signal_matching_no_metric_name_builds_fine():
    assert [s.name for s in _build(_signal("myOwnSignal")).signals] == ["myOwnSignal"]


def test_a_trigger_may_reference_a_metric_name_alone_or_alongside_a_declared_signal_but_never_an_unknown_name():
    metric = sorted(metric_names())[0]

    bare = _build(trigger=f"{metric} >= 50")
    assert bare.states["a"].actions[0].trigger == f"{metric} >= 50"
    assert bare.triggers_reference("a", {metric}) is True

    combined = _build(_signal("myOwnSignal"), trigger=f"signal.myOwnSignal >= 50 and {metric} >= 10")
    assert combined.states["a"].actions[0].trigger == f"signal.myOwnSignal >= 50 and {metric} >= 10"

    with pytest.raises(ValueError, match="undefined name"):
        _build(trigger="totallyUnknownName >= 50")
