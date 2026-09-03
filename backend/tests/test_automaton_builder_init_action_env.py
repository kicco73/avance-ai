"""AutomatonBuilder._build_init_action's own explicit `env:` mapping —
merged on top of every declared env key's own default, exactly like a
regular action's `env:` field (see test_automaton_builder_env_
declarations.py for the declared-defaults side of this).
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


def test_init_actions_own_env_overrides_the_declared_default_for_that_key():
    automaton = _build("""
env:
  greeting:
    value: "'default'"
init-action:
  target: a
  env:
    greeting: "'overridden'"
states:
  a:
    contextual-prompt: hi
""")
    assert automaton.init_action.env == {"greeting": "'overridden'"}


def test_init_actions_own_env_is_merged_with_other_declared_defaults_not_replacing_them():
    automaton = _build("""
env:
  a:
    value: "1"
  b:
    value: "2"
init-action:
  target: a
  env:
    a: "99"
states:
  a:
    contextual-prompt: hi
""")
    assert automaton.init_action.env == {"a": "99", "b": "2"}


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
