"""The project-level `env:` section (AutomatonBuilder._build_env_key) —
parsing each key's name/type/ai-definition. A key declares
what it holds and nothing else: its first value is its type's own default,
and a 'value' field fails the build like any other unknown one.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.model import ENV_DEFAULTS_ACTION_NAME, ENV_TYPE_DEFAULTS

pytestmark = pytest.mark.contract


def _build(env_yaml: str) -> object:
    content = f"""
project:
  id: test_project
{env_yaml}
init-action:
  target: a
states:
  a:
    input-processor: ai
    contextual-prompt: hi
"""
    return AutomatonBuilder().build({"index.yml": content})


def test_a_key_declares_a_type_and_takes_no_value_with_every_other_field_parsed_when_given():
    bare = _build("env:\n  visits:\n    type: number\n").env_keys[0]
    assert bare.name == "visits"
    assert bare.type == "number"
    assert bare.ai_definition is None

    described = _build(
        'env:\n  visits:\n    type: number\n    ai-definition: "How many times this state fired."\n'
    ).env_keys[0]
    assert described.ai_definition == "How many times this state fired."

    assert _build("").env_keys == []


def test_the_env_defaults_action_carries_each_keys_own_type_default_in_declaration_order():
    automaton = _build(
        "env:\n  visits:\n    type: number\n  name:\n    type: string\n"
        "  flag:\n    type: bool\n  slot:\n    type: list\n  total:\n    type: number\n"
    )
    action = automaton.env_defaults_action
    assert action.name == ENV_DEFAULTS_ACTION_NAME
    assert action.on_exit == "env.visits = 0\nenv.name = ''\nenv.flag = False\nenv.slot = []\nenv.total = 0"
    assert action.task is None
    declarable = ("number", "string", "bool", "list")
    assert {name: ENV_TYPE_DEFAULTS[name] for name in declarable} == {
        "number": 0, "string": "", "bool": False, "list": [],
    }
    assert _build("").env_defaults_action.on_exit is None


@pytest.mark.parametrize(("env_yaml", "match"), [
    ("env:\n  - not\n  - a\n  - mapping\n", "'env' must be a mapping"),
    ("env:\n  visits:\n    type: integer\n", "Env key 'visits': 'type' must be one of number, string, bool, list, got 'integer'"),
    ("env:\n  visits:\n    type: number\n    value: \"0\"\n", "'value'"),
    ("env:\n  visits:\n    type: number\n    ui-description: \"How many times.\"\n", "'ui-description'"),
    ("env:\n  slot:\n    type: list\n    value: \"['a']\"\n", "'value'"),
], ids=["not-a-mapping", "type-out-of-list", "value-on-a-key", "ui-description-on-a-key", "value-on-a-list-key"])
def test_build_rejects_a_malformed_section_a_wrong_type_or_a_key_that_declares_a_value(env_yaml, match):
    with pytest.raises(ValueError, match=match):
        _build(env_yaml)


def test_a_key_that_declares_no_type_builds_as_undeclared():
    key = _build("env:\n  nivel: {}\n").env_keys[0]
    assert key.name == "nivel"
    assert key.type == "undeclared"
