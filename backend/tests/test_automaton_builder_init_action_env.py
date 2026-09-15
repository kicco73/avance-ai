"""AutomatonBuilder._build_init_action's own explicit `env:` mapping —
the init-action's own writes, and nothing else: the declared keys' own
defaults are a separate action, Automaton.env_defaults_action (see
test_automaton_builder_env_declarations.py).
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(content: str) -> object:
    content = "project:\n  id: test_project\n" + content
    return AutomatonBuilder().build({"index.yml": content})


def test_init_action_can_declare_its_own_env():
    automaton = _build("""
env:
  greeting:
    type: string
    value: ""
init-action:
  target: a
  env:
    greeting: "'hi'"
states:
  a:
    contextual-prompt: hi
""")
    assert automaton.init_action.env == {"greeting": "'hi'"}


def test_init_actions_own_env_holds_only_its_own_writes_never_the_declared_defaults():
    automaton = _build("""
env:
  a:
    type: number
    value: "1"
  b:
    type: number
    value: "2"
init-action:
  target: a
  env:
    a: "99"
states:
  a:
    contextual-prompt: hi
""")
    assert automaton.init_action.env == {"a": "99"}
    assert automaton.env_defaults_action.env == {"a": "1", "b": "2"}


def test_an_init_action_without_env_has_none_even_when_keys_are_declared():
    automaton = _build("""
env:
  a:
    type: number
    value: "1"
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
""")
    assert automaton.init_action.env is None


def test_init_actions_own_env_writing_to_an_undeclared_key_is_rejected():
    with pytest.raises(ValueError, match="not declared"):
        _build("""
init-action:
  target: a
  env:
    never_declared: "1"
states:
  a:
    contextual-prompt: hi
""")


def test_init_actions_own_env_must_match_the_keys_declared_type():
    with pytest.raises(ValueError, match="is a string, but 'a' is declared number"):
        _build("""
env:
  a:
    type: number
init-action:
  target: a
  env:
    a: "'one'"
states:
  a:
    contextual-prompt: hi
""")
