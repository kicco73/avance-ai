"""The project-level `env:` section (AutomatonBuilder._build_env_key) —
parsing each key's name/ui-description/value, and validating that
`value` is a valid, resolvable expression.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

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
    bare = _build("env:\n  visits:\n").env_keys[0]
    assert bare.name == "visits"
    assert bare.value == ""
    assert bare.ui_description is None

    described = _build('env:\n  visits:\n    ui-description: "How many times this fired."\n    value: "0"\n').env_keys[0]
    assert described.ui_description == "How many times this fired."
    assert described.value == "0"

    assert _build("env:\n  enabled:\n    value: true\n").env_keys[0].value == "True"
    assert _build("").env_keys == []


def test_a_keys_own_value_may_reference_an_earlier_declared_key(self=None):
    by_name = {e.name: e for e in _build('env:\n  visits:\n    value: "0"\n  last_visit_count:\n    value: env.visits\n').env_keys}
    assert by_name["last_visit_count"].value == "env.visits"


@pytest.mark.parametrize(("env_yaml", "match"), [
    ("env:\n  - not\n  - a\n  - mapping\n", "'env' must be a mapping"),
    ("env:\n  visits:\n    value: env.never_declared\n", r"undefined name\(s\).*env.never_declared"),
    ('env:\n  broken:\n    value: "1 +"\n', "is not a valid expression"),
    ('env:\n  last_visit_count:\n    value: env.visits\n  visits:\n    value: "0"\n', "references env.visits before it's declared"),
    ("env:\n  visits:\n    value: env.visits\n", "references env.visits before it's declared"),
], ids=["not-a-mapping", "undeclared-reference", "invalid-expression", "forward-reference", "self-reference"])
def test_build_rejects_a_malformed_section_or_a_value_that_cannot_resolve_in_declaration_order(env_yaml, match):
    with pytest.raises(ValueError, match=match):
        _build(env_yaml)
