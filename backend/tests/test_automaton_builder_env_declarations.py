"""The project-level `env:` section (AutomatonBuilder._build_env_key) —
parsing each key's name/type/ui-description/value, and validating that
`value` is a valid, resolvable expression of the declared type.
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
    contextual-prompt: hi
"""
    return AutomatonBuilder().build({"index.yml": content})


def test_a_key_defaults_to_an_empty_value_and_no_description_with_every_field_parsed_when_given():
    bare = _build("env:\n  visits:\n    type: number\n").env_keys[0]
    assert bare.name == "visits"
    assert bare.type == "number"
    assert bare.value == ""
    assert bare.ui_description is None
    assert bare.ai_definition is None

    described = _build(
        'env:\n  visits:\n    type: number\n    ui-description: "How many times this fired."\n    value: "0"\n'
        '    ai-definition: "How many times this state fired."\n'
    ).env_keys[0]
    assert described.ui_description == "How many times this fired."
    assert described.value == "0"
    assert described.ai_definition == "How many times this state fired."

    assert _build("env:\n  enabled:\n    type: bool\n    value: true\n").env_keys[0].value == "True"
    assert _build("").env_keys == []


def test_a_keys_own_value_may_reference_an_earlier_declared_key():
    by_name = {
        e.name: e for e in _build(
            'env:\n  visits:\n    type: number\n    value: "0"\n  last_visit_count:\n    type: number\n    value: env.visits\n'
        ).env_keys
    }
    assert by_name["last_visit_count"].value == "env.visits"


def test_the_env_defaults_action_carries_each_keys_value_or_its_types_own_default_in_declaration_order():
    automaton = _build(
        "env:\n  visits:\n    type: number\n    value: \"3\"\n  name:\n    type: string\n"
        "  flag:\n    type: bool\n  slot:\n    type: choice\n  total:\n    type: number\n"
    )
    action = automaton.env_defaults_action
    assert action.name == ENV_DEFAULTS_ACTION_NAME
    assert action.env == {"visits": "3", "name": "''", "flag": "False", "slot": "[]", "total": "0"}
    assert action.task is None and action.on_exit is None
    assert ENV_TYPE_DEFAULTS == {"number": 0, "string": "", "bool": False, "choice": []}
    assert _build("").env_defaults_action.env is None


@pytest.mark.parametrize(("env_yaml", "match"), [
    ("env:\n  - not\n  - a\n  - mapping\n", "'env' must be a mapping"),
    ("env:\n  visits:\n    value: \"0\"\n", "Env key 'visits': 'type' is required and must be one of number, string, bool, choice"),
    ("env:\n  visits: {}\n", "Env key 'visits': 'type' is required and must be one of number, string, bool, choice"),
    ("env:\n  visits:\n    type: integer\n", "Env key 'visits': 'type' is required and must be one of number, string, bool, choice, got 'integer'"),
    ("env:\n  visits:\n    type: number\n    value: \"'many'\"\n", "env key 'visits' is declared number but its 'value' \\('\'many\''\\) is a string"),
    ("env:\n  enabled:\n    type: bool\n    value: \"1\"\n", "env key 'enabled' is declared bool but its 'value' \\('1'\\) is a number"),
    ("env:\n  slot:\n    type: choice\n    value: \"['a']\"\n", "env key 'slot': a choice key takes no 'value'"),
    ("env:\n  visits:\n    type: number\n    value: env.never_declared\n", r"undefined name\(s\).*env.never_declared"),
    ('env:\n  broken:\n    type: number\n    value: "1 +"\n', "is not a valid expression"),
    ('env:\n  last_visit_count:\n    type: number\n    value: env.visits\n  visits:\n    type: number\n    value: "0"\n', "references env.visits before it's declared"),
    ("env:\n  visits:\n    type: number\n    value: env.visits\n", "references env.visits before it's declared"),
], ids=[
    "not-a-mapping", "missing-type", "missing-type-bare-key", "type-out-of-list", "string-value-on-number",
    "number-value-on-bool", "value-on-choice", "undeclared-reference", "invalid-expression",
    "forward-reference", "self-reference",
])
def test_build_rejects_a_malformed_section_a_missing_or_wrong_type_or_a_value_that_cannot_resolve(env_yaml, match):
    with pytest.raises(ValueError, match=match):
        _build(env_yaml)
