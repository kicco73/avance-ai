"""Action-level `env:` (see automaton_builder.py's _build_action/
_build_action_env) — a mapping of env-key -> expression, evaluated (see
Automaton.eval_action_env) whenever the action fires and merged onto
chat.env.Env's own persisted store. Unlike `trigger`, an env expression's
whole scope isn't statically known at build time (it may reference a
project's own free-form env key), so build-time validation here only
ever checks syntax, never unknown names — see test_automaton_builder_
env_triggers.py for the equivalent trigger-side behavior this
deliberately diverges from.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(action_yaml: str):
    content = f"""
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
{action_yaml}
  b:
    contextual-prompt: there
"""
    return AutomatonBuilder().build({"index.yml": content})


def test_env_field_is_parsed_as_a_key_to_expression_mapping():
    automaton = _build("""
        env:
          reset_counter: True
          number_of_steps: number_of_steps + 1
""")

    action = automaton.states["a"].actions[0]
    assert action.env == {"reset_counter": "True", "number_of_steps": "number_of_steps + 1"}


def test_non_string_yaml_values_are_normalized_to_expression_source():
    automaton = _build("""
        env:
          enabled: true
          count: 42
          nothing: null
""")

    action = automaton.states["a"].actions[0]
    assert action.env == {"enabled": "True", "count": "42", "nothing": "None"}


def test_no_env_field_leaves_it_as_none():
    automaton = _build("")

    assert automaton.states["a"].actions[0].env is None


def test_an_empty_env_mapping_is_normalized_to_none():
    automaton = _build("""
        env: {}
""")

    assert automaton.states["a"].actions[0].env is None


def test_env_referencing_a_declared_signal_is_valid():
    content = """
signals:
  mySignal:
    definition: "Some domain-specific signal."
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        env:
          last_signal: mySignal
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].env == {"last_signal": "mySignal"}


def test_env_referencing_an_undeclared_free_form_env_key_is_allowed():
    """Unlike a trigger (see test_automaton_builder_env_triggers.py),
    this must NOT raise — self-referencing a not-yet-declared free-form
    env key (e.g. a running counter) is the feature's own core use case,
    not a typo."""
    automaton = _build("""
        env:
          number_of_steps: number_of_steps + 1
""")

    assert automaton.states["a"].actions[0].env == {"number_of_steps": "number_of_steps + 1"}


def test_env_with_a_syntactically_invalid_expression_is_rejected():
    with pytest.raises(ValueError, match="is not a valid expression"):
        _build("""
        env:
          broken: "1 +"
""")


def test_env_must_be_a_mapping():
    with pytest.raises(ValueError, match="'env' must be a mapping"):
        _build("""
        env:
          - not
          - a
          - mapping
""")
