"""Action-level `env:` (see automaton_builder.py's _build_action/
_build_action_env) — a mapping of env-key -> expression, evaluated (see
Automaton.eval_action_env) whenever the action fires and merged onto
tracking.env.Env's own action_set() store. Unlike before this refactor,
an env expression now gets the exact same build-time validation a
trigger does (see _validate_namespaced_expression) — every `env.<name>`
reference is checked against every action's own declared `env:` key,
project-wide (see AutomatonBuilder.build's own two-pass collection),
which is what makes a running counter's own self-reference (`env.
number_of_steps: env.number_of_steps + 1`) valid: the key it references
is declared right there, by this very action's own env: mapping.
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
          number_of_steps: env.number_of_steps + 1
""")

    action = automaton.states["a"].actions[0]
    assert action.env == {"reset_counter": "True", "number_of_steps": "env.number_of_steps + 1"}


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
          last_signal: signal.mySignal
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].env == {"last_signal": "signal.mySignal"}


def test_env_referencing_an_undeclared_env_key_is_rejected():
    """Unlike before this refactor, an env expression now gets the same
    unknown-name validation a trigger does (see this module's own
    docstring) — a running counter's own self-reference is fine (see
    test_env_field_is_parsed_as_a_key_to_expression_mapping, where the
    key it references is declared right there), but a genuinely
    undeclared `env.` key is a build error, not a routine no-op."""
    with pytest.raises(ValueError, match="undefined name\\(s\\).*env.never_declared_anywhere"):
        _build("""
        env:
          last_value: env.never_declared_anywhere
""")


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
