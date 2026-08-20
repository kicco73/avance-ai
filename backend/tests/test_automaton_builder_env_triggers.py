"""A trigger may reference any of SystemFacts'/SessionFacts' own methods
(see automaton.identifier_registry.SYSTEM/SESSION) as `system.<name>()`/
`session.<name>()`, without failing build-time validation, the same way
`signal.<name>` and a core metric name already do — but not an
arbitrary free-form [env] key (see tracking.env.Env's own stored()),
since those are only ever known at runtime; only a project's own
*declared* `env:` keys (the project-level `env:` section, parallel to
`signals:` — see automaton.automaton.EnvKey/AutomatonBuilder.build's own
env_keys) are valid `env.<name>` references (see automaton_builder.py's
own _actions_sanity_check/_validate_namespaced_expression docstrings).
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.identifier_registry import SESSION, SYSTEM

pytestmark = pytest.mark.contract


def _project(trigger: str) -> str:
    return f"""
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


@pytest.mark.parametrize("attr", sorted(SYSTEM))
def test_a_trigger_may_reference_any_system_attr(attr):
    content = _project(f"system.{attr}() != None")
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].trigger == f"system.{attr}() != None"


@pytest.mark.parametrize("attr", sorted(SESSION))
def test_a_trigger_may_reference_any_session_attr(attr):
    content = _project(f"session.{attr}() != None")
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].trigger == f"session.{attr}() != None"


def test_a_trigger_referencing_an_undeclared_env_key_is_rejected():
    content = _project("env.some_custom_env_key >= 1")
    with pytest.raises(ValueError, match="undefined name\\(s\\).*env.some_custom_env_key"):
        AutomatonBuilder().build({"index.yml": content})


def test_a_trigger_may_reference_a_declared_env_key():
    content = f"""
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
    """The pre-migration bare-name syntax (`mood >= 50` instead of
    `signal.mood >= 50`) must fail loudly, not silently resolve to
    nothing — see the "Migrazione" note in this refactor's own spec."""
    content = f"""
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
    """See automaton.trigger_type_violations' own docstring — system.
    today() is a date string (see identifier_registry.SYSTEM), never a
    number, so comparing it with `>=` would raise a genuine TypeError the
    moment this trigger is ever evaluated; build-time validation can
    catch that statically, unlike a signal's actual runtime value."""
    content = _project_with_mood_signal("system.today() >= 5")
    with pytest.raises(ValueError, match="system.today\\(\\).*>=.*5"):
        AutomatonBuilder().build({"index.yml": content})


def test_a_trigger_with_a_type_consistent_numeric_threshold_builds_fine():
    content = _project_with_mood_signal("signal.mood >= 75")
    automaton = AutomatonBuilder().build({"index.yml": content})
    assert automaton.states["a"].actions[0].trigger == "signal.mood >= 75"
