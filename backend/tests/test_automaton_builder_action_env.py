"""Action-level `env:` — a mapping of env-key -> expression, evaluated
whenever the action fires. Both what an expression reads and what a key
writes to must already be declared in the project's `env:` section.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


def _build(action_yaml: str, env_section: str = "", top_section: str = ""):
    content = f"""
project:
  id: test_project
{top_section}
{env_section}
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
{action_yaml}
  b:
    contextual-prompt: there
"""
    return AutomatonBuilder().build({"index.yml": content})


def _action_env(action_yaml: str, env_section: str = "", top_section: str = ""):
    return _build(action_yaml, env_section, top_section).states["a"].actions[0].env


def _env_write(key: str, expression: str) -> str:
    return f"        env:\n          {key}: {expression}\n"


def _declared(key: str, value: str | None = None) -> str:
    return f"env:\n  {key}:\n    value: {value}\n" if value is not None else f"env:\n  {key}: {{}}\n"


def test_env_is_parsed_as_a_key_to_expression_source_mapping_or_none_when_absent_or_empty():
    two_keys = _action_env(
        "        env:\n          reset_counter: True\n          number_of_steps: env.number_of_steps + 1\n",
        "env:\n  reset_counter: {}\n  number_of_steps: {}\n",
    )
    assert two_keys == {"reset_counter": "True", "number_of_steps": "env.number_of_steps + 1"}

    normalized = _action_env(
        "        env:\n          enabled: true\n          count: 42\n          nothing: null\n",
        "env:\n  enabled: {}\n  count: {}\n  nothing: {}\n",
    )
    assert normalized == {"enabled": "True", "count": "42", "nothing": "None"}

    assert _action_env("") is None
    assert _action_env("        env: {}\n") is None

    with_signal = _action_env(
        _env_write("last_signal", "signal.mySignal"), _declared("last_signal"),
        top_section='signals:\n  mySignal:\n    definition: "Some domain-specific signal."\n',
    )
    assert with_signal == {"last_signal": "signal.mySignal"}


@pytest.mark.parametrize(("action_yaml", "env_section", "match"), [
    (_env_write("last_value", "env.never_declared_anywhere"), _declared("last_value"), r"undefined name\(s\).*env.never_declared_anywhere"),
    (_env_write("never_declared_anywhere", '"1"'), "", "env key 'never_declared_anywhere' is not declared"),
    (_env_write("broken", '"1 +"'), _declared("broken"), "is not a valid expression"),
    ("        env:\n          - not\n          - a\n          - mapping\n", "", "'env' must be a mapping"),
    (_env_write("greeting", '"42"'), _declared("greeting", "\"'hello'\""), "is a number, but 'greeting' was declared as a string"),
    (_env_write("counter", "\"'not a number'\""), _declared("counter", '"0"'), "is a string, but 'counter' was declared as a number"),
    (_env_write("enabled", '"2"'), _declared("enabled", '"True"'), "is a number, but 'enabled' was declared as a bool"),
], ids=[
    "undeclared-read", "undeclared-write", "invalid-expression", "not-a-mapping",
    "number-to-string", "string-to-number", "number-to-bool",
])
def test_build_rejects_undeclared_reads_or_writes_invalid_expressions_non_mappings_and_type_drift(action_yaml, env_section, match):
    """The write side: an action's `env:` field cannot introduce a new key
    just by writing to it. bool and number are kept strictly separate —
    unlike the ordering-comparison leniency (True >= 0.5 is legal Python),
    a flag switching to holding an arbitrary count is exactly the kind of
    drift this check exists to catch."""
    with pytest.raises(ValueError, match=match):
        _build(action_yaml, env_section)


def test_a_matching_type_an_untyped_key_and_a_statically_unknowable_expression_are_all_accepted():
    """An empty 'value' never establishes a type to begin with, so any
    expression is accepted; `env.other` reads another key at runtime — its
    own kind isn't knowable ahead of a real turn, so the check is silently
    skipped rather than guessing wrong."""
    assert _action_env(_env_write("counter", '"5"'), _declared("counter", '"0"')) == {"counter": "5"}
    assert _action_env(_env_write("anything", "\"'a string now'\""), _declared("anything")) == {"anything": "'a string now'"}
    assert _action_env(_env_write("counter", "env.other"), "env:\n  counter:\n    value: \"0\"\n  other: {}\n") == {"counter": "env.other"}
