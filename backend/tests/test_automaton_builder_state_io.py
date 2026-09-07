"""A state's own `input`/`output` (AutomatonBuilder._build_variable_name_list
+ AutomatonValidator.validate_state_io) — each name must already be
declared in the project's own `env:` section, and that env key must carry
its own `ai-definition` once any state actually lists it.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(env_yaml: str, state_yaml: str) -> object:
    content = f"""
project:
  id: test_project
{env_yaml}
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
{state_yaml}
"""
    return AutomatonBuilder().build({"index.yml": content})


def test_input_and_output_select_already_declared_env_variables():
    automaton = _build(
        "env:\n  customer_record:\n    ai-definition: Customer data.\n"
        "  intent:\n    ai-definition: The customer's intent.\n"
        "  confidence:\n    ai-definition: 0-100.\n",
        "    input:\n      - customer_record\n    output:\n      - intent\n      - confidence\n",
    )
    state = automaton.states["a"]
    assert state.input == ("customer_record",)
    assert state.output == ("intent", "confidence")


def test_input_referencing_an_undeclared_env_variable_is_rejected():
    with pytest.raises(ValueError, match="input 'missing'.*not.*declared"):
        _build("env:\n  known:\n    ai-definition: x\n", "    input:\n      - missing\n")


def test_output_referencing_an_undeclared_env_variable_is_rejected():
    with pytest.raises(ValueError, match="output 'missing'.*not.*declared"):
        _build("env:\n  known:\n    ai-definition: x\n", "    output:\n      - missing\n")


def test_a_variable_used_as_input_or_output_requires_its_own_ai_definition():
    with pytest.raises(ValueError, match="'ai-definition'"):
        _build("env:\n  customer_record:\n    value: ''\n", "    input:\n      - customer_record\n")


def test_input_and_output_must_be_lists_of_strings():
    with pytest.raises(ValueError, match="'input' must be a list"):
        _build("env:\n  known:\n    ai-definition: x\n", "    input: known\n")
