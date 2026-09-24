"""An action's `on-exit` env writes, `env.<key> = <expression>`, checked
at build time. Both what an expression reads and what a key writes to
must already be declared in the project's `env:` section.
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
    input-processor: ai
    contextual-prompt: hi
    actions:
      - name: go
        target: b
{action_yaml}
  b:
    input-processor: ai
    contextual-prompt: there
"""
    return AutomatonBuilder().build({"index.yml": content})


def _on_exit(action_yaml: str, env_section: str = "", top_section: str = ""):
    return _build(action_yaml, env_section, top_section).states["a"].actions[0].on_exit


def _env_write(key: str, expression: str) -> str:
    return f"        on-exit: env.{key} = {expression}\n"


def _declared(key: str, type: str) -> str:
    return f"env:\n  {key}:\n    type: {type}\n"


def test_an_on_exit_write_may_read_a_signal():
    assert _on_exit(
        _env_write("last_signal", "signal.mySignal"), _declared("last_signal", "number"),
        top_section='signals:\n  mySignal:\n    definition: "Some domain-specific signal."\n',
    ) == "env.last_signal = signal.mySignal"


def test_an_action_env_mapping_is_refused_by_a_build():
    with pytest.raises(ValueError, match="'env' is not a field an action has"):
        _build("        env:\n          counter: 1\n", _declared("counter", "number"))


@pytest.mark.parametrize(("action_yaml", "env_section", "match"), [
    (_env_write("last_value", "env.never_declared_anywhere"), _declared("last_value", "string"), r"undefined name\(s\).*env.never_declared_anywhere"),
    (_env_write("never_declared_anywhere", "1"), "", "env key 'never_declared_anywhere' is not declared"),
    (_env_write("greeting", "42"), _declared("greeting", "string"), "is a number, but 'greeting' is declared string"),
    (_env_write("counter", "'not a number'"), _declared("counter", "number"), "is a string, but 'counter' is declared number"),
    (_env_write("enabled", "2"), _declared("enabled", "bool"), "is a number, but 'enabled' is declared bool"),
    (_env_write("slot", "'not a list'"), _declared("slot", "list"), "is a string, but 'slot' is declared list"),
], ids=[
    "undeclared-read", "undeclared-write", "number-to-string", "string-to-number", "number-to-bool", "string-to-list",
])
def test_build_rejects_undeclared_reads_or_writes_and_type_drift(action_yaml, env_section, match):
    """The write side: an action's `on-exit` cannot introduce a new key
    just by writing to it. bool and number are kept strictly separate —
    unlike the ordering-comparison leniency (True >= 0.5 is legal Python),
    a flag switching to holding an arbitrary count is exactly the kind of
    drift this check exists to catch."""
    with pytest.raises(ValueError, match=match):
        _build(action_yaml, env_section)


def test_a_matching_type_and_a_statically_unknowable_expression_are_both_accepted():
    """`env.other` reads another key at runtime — its own kind isn't
    knowable ahead of a real turn, so the check is silently skipped
    rather than guessing wrong."""
    assert _on_exit(_env_write("counter", "5"), _declared("counter", "number")) == "env.counter = 5"
    assert _on_exit(_env_write("anything", "'a string now'"), _declared("anything", "string")) == "env.anything = 'a string now'"
    assert _on_exit(_env_write("slot", "['a', 'b']"), _declared("slot", "list")) == "env.slot = ['a', 'b']"
    assert _on_exit(
        _env_write("counter", "env.other"), "env:\n  counter:\n    type: number\n  other:\n    type: number\n"
    ) == "env.counter = env.other"


def test_a_key_stored_without_a_type_takes_a_write_of_any_kind():
    """A revision saved before env keys declared their type keeps
    building: its keys are undeclared, and an undeclared key is the one
    thing a write can never drift from."""
    undeclared = "env:\n  nivel: {}\n"
    assert _on_exit(_env_write("nivel", "3"), undeclared) == "env.nivel = 3"
    assert _on_exit(_env_write("nivel", "'alto'"), undeclared) == "env.nivel = 'alto'"
    assert _on_exit(_env_write("nivel", "True"), undeclared) == "env.nivel = True"
