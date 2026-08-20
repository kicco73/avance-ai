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
{env_yaml}
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
"""
    return AutomatonBuilder().build({"index.yml": content})


def test_a_declared_key_with_no_fields_at_all_defaults_to_an_empty_value_and_no_description():
    automaton = _build("""
env:
  visits:
""")
    env_key = automaton.env_keys[0]
    assert env_key.name == "visits"
    assert env_key.value == ""
    assert env_key.ui_description is None


def test_ui_description_and_value_are_parsed():
    automaton = _build("""
env:
  visits:
    ui-description: "How many times this fired."
    value: "0"
""")
    env_key = automaton.env_keys[0]
    assert env_key.ui_description == "How many times this fired."
    assert env_key.value == "0"


def test_non_string_yaml_value_is_normalized_to_expression_source():
    automaton = _build("""
env:
  enabled:
    value: true
""")
    assert automaton.env_keys[0].value == "True"


def test_no_env_section_leaves_env_keys_empty():
    automaton = _build("")
    assert automaton.env_keys == []


def test_env_section_must_be_a_mapping():
    with pytest.raises(ValueError, match="'env' must be a mapping"):
        _build("""
env:
  - not
  - a
  - mapping
""")


def test_a_keys_own_value_may_reference_another_declared_key():
    automaton = _build("""
env:
  visits:
    value: "0"
  last_visit_count:
    value: env.visits
""")
    by_name = {e.name: e for e in automaton.env_keys}
    assert by_name["last_visit_count"].value == "env.visits"


def test_a_keys_own_value_referencing_an_undeclared_key_is_rejected():
    with pytest.raises(ValueError, match="undefined name\\(s\\).*env.never_declared"):
        _build("""
env:
  visits:
    value: env.never_declared
""")


def test_a_keys_own_value_with_a_syntactically_invalid_expression_is_rejected():
    with pytest.raises(ValueError, match="is not a valid expression"):
        _build("""
env:
  broken:
    value: "1 +"
""")
