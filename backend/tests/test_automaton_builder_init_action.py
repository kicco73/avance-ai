"""init-action's own fields, read straight off the built Automaton —
same fields a real action has, minus trigger/env (see
AutomatonYamlEditor._init_action_payload's own docstring for why those
two don't apply)."""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def test_ui_description_is_read_from_the_init_action():
    content = """
project:
  id: test_project
init-action:
  target: a
  ui-description: Where every session begins.
states:
  a:
    contextual-prompt: hi
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.init_action.ui_description == "Where every session begins."


def test_ui_description_absent_on_the_init_action_is_none():
    content = """
project:
  id: test_project
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.init_action.ui_description is None
