"""A trigger may reference any of chat.env.Env's always-computed keys
(see automaton_builder.ENV_COMPUTED_KEYS) without failing build-time
validation, the same way signal/metric names already do — but not an
arbitrary free-form [env] key, since those are only ever known at
runtime (see automaton_builder.py's own _actions_sanity_check docstring).
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import ENV_COMPUTED_KEYS, AutomatonBuilder


@pytest.mark.parametrize("env_key", ENV_COMPUTED_KEYS)
def test_a_trigger_may_reference_any_env_computed_key(env_key):
    content = f"""
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        trigger: "{env_key} >= 0"
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].trigger == f"{env_key} >= 0"


def test_a_trigger_referencing_an_undeclared_free_form_env_key_is_rejected():
    content = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        trigger: "some_custom_env_key >= 1"
  b:
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match="undefined signal\\(s\\)/metric\\(s\\)/env value\\(s\\)"):
        AutomatonBuilder().build({"index.yml": content})
