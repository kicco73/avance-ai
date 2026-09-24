"""The init-action's own `on-exit` env writes — the init-action's own
writes, and nothing else: the declared keys' own defaults are a separate
action, Automaton.env_defaults_action (see
test_automaton_builder_env_declarations.py).
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(content: str) -> object:
    content = "project:\n  id: test_project\n" + content
    return AutomatonBuilder().build({"index.yml": content})


def test_init_actions_own_on_exit_holds_only_its_own_writes_never_the_declared_defaults():
    automaton = _build("""
env:
  a:
    type: number
  b:
    type: number
init-action:
  target: a
  on-exit: env.a = 99
states:
  a:
    input-processor: ai
    contextual-prompt: hi
""")
    assert automaton.init_action.on_exit == "env.a = 99"
    assert automaton.env_defaults_action.on_exit == "env.a = 0\nenv.b = 0"


def test_init_actions_own_on_exit_writing_to_an_undeclared_key_is_rejected():
    with pytest.raises(ValueError, match="not declared"):
        _build("""
init-action:
  target: a
  on-exit: env.never_declared = 1
states:
  a:
    input-processor: ai
    contextual-prompt: hi
""")


def test_init_actions_own_on_exit_must_match_the_keys_declared_type():
    with pytest.raises(ValueError, match="is a string, but 'a' is declared number"):
        _build("""
env:
  a:
    type: number
init-action:
  target: a
  on-exit: env.a = 'one'
states:
  a:
    input-processor: ai
    contextual-prompt: hi
""")


def test_an_init_action_env_mapping_is_refused_by_a_build():
    with pytest.raises(ValueError, match="'env' is not a field an action has"):
        _build("""
env:
  a:
    type: number
init-action:
  target: a
  env:
    a: "1"
states:
  a:
    input-processor: ai
    contextual-prompt: hi
""")
