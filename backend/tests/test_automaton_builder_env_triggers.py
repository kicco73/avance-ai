"""A trigger may reference `system.<name>()`/`session.<name>()` and
`signal.<name>` freely, but `env.<name>` references must match a key
already declared in the project's top-level `env:` section.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.identifier_registry import IdentifierRegistry

pytestmark = pytest.mark.contract


def _project(trigger: str) -> str:
    return f"""
project:
  id: test_project
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        trigger: "{trigger}"
  b:
    contextual-prompt: there
"""


def test_a_trigger_may_not_reference_the_retired_system_namespace():
    """`system.*` (today/time) is gone — a bare name outside every
    reserved namespace, so it fails validation like any other unknown."""
    content = _project("system.today() != None")
    with pytest.raises(ValueError, match="undefined name"):
        AutomatonBuilder().build({"index.yml": content})


@pytest.mark.parametrize("attr", sorted(IdentifierRegistry.SESSION))
def test_a_trigger_may_reference_any_session_attr(attr):
    content = _project(f"session.{attr}() != None")
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].trigger == f"session.{attr}() != None"


@pytest.mark.parametrize("attr", sorted(IdentifierRegistry.USER))
def test_a_trigger_may_reference_any_user_attr(attr):
    """Unlike system.*/session.*, user.* is a plain field, not a
    zero-arg call — same shape as env.*."""
    content = _project(f"user.{attr} != None")
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].trigger == f"user.{attr} != None"


def test_a_trigger_referencing_an_unknown_user_attr_is_rejected():
    content = _project("user.bogus_field != None")
    with pytest.raises(ValueError, match="undefined name\\(s\\).*user.bogus_field"):
        AutomatonBuilder().build({"index.yml": content})


def test_a_trigger_referencing_an_unknown_source_is_rejected():
    """`source.<name>.<method>(...)` is a dynamic, per-project namespace
    (like automaton.<project>.*) — see test_automaton_builder_sources.py
    for full coverage of what a *declared* source may be referenced as."""
    content = _project("source.bogus_source.read() != None")
    with pytest.raises(ValueError, match="undefined name\\(s\\).*source.bogus_source"):
        AutomatonBuilder().build({"index.yml": content})


def test_a_trigger_referencing_an_undeclared_env_key_is_rejected():
    content = _project("env.some_custom_env_key >= 1")
    with pytest.raises(ValueError, match="undefined name\\(s\\).*env.some_custom_env_key"):
        AutomatonBuilder().build({"index.yml": content})


def test_a_trigger_may_reference_a_declared_env_key():
    content = f"""
project:
  id: test_project
env:
  visits: {{}}
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        trigger: "env.visits >= 1"
  b:
    contextual-prompt: there
"""
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].trigger == "env.visits >= 1"


def test_a_trigger_referencing_a_leftover_bare_signal_name_is_rejected():
    """A bare name (`mood >= 50` instead of `signal.mood >= 50`) must
    fail loudly, not silently resolve to nothing."""
    content = f"""
project:
  id: test_project
init-action:
  target: a
signals:
  mood:
    definition: how happy the user seems
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        trigger: "mood >= 50"
  b:
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match="undefined name\\(s\\).*mood"):
        AutomatonBuilder().build({"index.yml": content})


def _project_with_mood_signal(trigger: str) -> str:
    return f"""
project:
  id: test_project
init-action:
  target: a
signals:
  mood:
    definition: how happy the user seems
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        trigger: "{trigger}"
  b:
    contextual-prompt: there
"""


def test_a_trigger_comparing_a_string_typed_identifier_against_a_number_is_rejected():
    """user.name is a string, never a number, so comparing it
    with `>=` is caught statically at build time."""
    content = _project_with_mood_signal("user.name >= 5")
    with pytest.raises(ValueError, match="user.name.*>=.*5"):
        AutomatonBuilder().build({"index.yml": content})


def test_a_trigger_with_a_type_consistent_numeric_threshold_builds_fine():
    content = _project_with_mood_signal("signal.mood >= 75")
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].trigger == "signal.mood >= 75"
