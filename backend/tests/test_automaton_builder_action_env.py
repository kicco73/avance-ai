"""Action-level `env:` — a mapping of env-key -> expression, evaluated
whenever the action fires. Both what an expression reads and what a key
writes to must already be declared in the project's `env:` section.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(action_yaml: str, env_section: str = ""):
    content = f"""
{env_section}
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
    automaton = _build(
        """
        env:
          reset_counter: True
          number_of_steps: env.number_of_steps + 1
""",
        env_section="""
env:
  reset_counter: {}
  number_of_steps: {}
""",
    )

    action = automaton.states["a"].actions[0]
    assert action.env == {"reset_counter": "True", "number_of_steps": "env.number_of_steps + 1"}


def test_non_string_yaml_values_are_normalized_to_expression_source():
    automaton = _build(
        """
        env:
          enabled: true
          count: 42
          nothing: null
""",
        env_section="""
env:
  enabled: {}
  count: {}
  nothing: {}
""",
    )

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
env:
  last_signal: {}
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
    """A genuinely undeclared `env.` read is a build error. `last_value`
    is declared, isolating this from the write-side check below."""
    with pytest.raises(ValueError, match="undefined name\\(s\\).*env.never_declared_anywhere"):
        _build(
            """
        env:
          last_value: env.never_declared_anywhere
""",
            env_section="""
env:
  last_value: {}
""",
        )


def test_env_write_to_an_undeclared_key_is_rejected():
    """The write side: an action's `env:` field cannot introduce a new
    key just by writing to it; the key must already be declared."""
    with pytest.raises(ValueError, match="env key 'never_declared_anywhere' is not declared"):
        _build("""
        env:
          never_declared_anywhere: "1"
""")


def test_env_with_a_syntactically_invalid_expression_is_rejected():
    with pytest.raises(ValueError, match="is not a valid expression"):
        _build(
            """
        env:
          broken: "1 +"
""",
            env_section="""
env:
  broken: {}
""",
        )


def test_env_must_be_a_mapping():
    with pytest.raises(ValueError, match="'env' must be a mapping"):
        _build("""
        env:
          - not
          - a
          - mapping
""")
