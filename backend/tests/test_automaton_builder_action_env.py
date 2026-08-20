"""Action-level `env:` (see automaton_builder.py's _build_action/
_build_action_env) — a mapping of env-key -> expression, evaluated (see
Automaton.eval_action_env) whenever the action fires and merged onto
tracking.env.Env's own action_set() store. An env expression gets the
exact same build-time validation a trigger does (see
_validate_namespaced_expression) — every `env.<name>` reference (on
either side: what an expression *reads*, and what an action's own env:
key itself *writes* to — see _actions_sanity_check's own declared-key
check) is checked against the project's own declared `env:` section
(parallel to `signals:`, see EnvKey/AutomatonBuilder.build's own
env_keys) — an action's `env:` field can no longer introduce a brand
new key on the fly just by writing to it.
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
    """A genuinely undeclared `env.` *read* is still a build error, not a
    routine no-op — `last_value` itself is declared here so this test
    isolates the read-side check (see the module docstring's own
    write-side counterpart, test_env_write_to_an_undeclared_key_is_
    rejected below) from the RHS reference to `never_declared_anywhere`,
    which is not."""
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
    """The write side of the same contract — unlike before this refactor,
    an action's own `env:` field can no longer introduce a brand new key
    just by writing to it; the key itself must already be declared in
    the project's own top-level `env:` section."""
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
