"""A trigger may reference `session.<name>()` and `signal.<name>` freely,
but `env.<name>` references must match a key already declared in the
project's top-level `env:` section.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.identifier_registry import IdentifierRegistry

pytestmark = pytest.mark.contract


def _project(trigger: str, top_yaml: str = "") -> str:
    return f"""
project:
  id: test_project
{top_yaml}init-action:
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


MOOD_SIGNAL = "signals:\n  mood:\n    definition: how happy the user seems\n"


def _build(trigger: str, top_yaml: str = "") -> str:
    return AutomatonBuilder().build({"index.yml": _project(trigger, top_yaml)}).states["a"].actions[0].trigger


@pytest.mark.parametrize("trigger", [
    *(f"session.{attr}() != None" for attr in sorted(IdentifierRegistry.SESSION)),
    *(f"user.{attr} != None" for attr in sorted(IdentifierRegistry.USER)),
])
def test_a_trigger_may_reference_any_session_call_or_user_field(trigger):
    """Unlike session.*, user.* is a plain field, not a zero-arg call —
    same shape as env.*."""
    assert _build(trigger) == trigger


@pytest.mark.parametrize(("trigger", "top_yaml", "match"), [
    ("system.today() != None", "", "undefined name"),
    ("user.bogus_field != None", "", r"undefined name\(s\).*user.bogus_field"),
    ("source.bogus_source.read() != None", "", r"undefined name\(s\).*source.bogus_source"),
    ("env.some_custom_env_key >= 1", "", r"undefined name\(s\).*env.some_custom_env_key"),
    ("mood >= 50", MOOD_SIGNAL, r"undefined name\(s\).*mood"),
    ("user.name >= 5", MOOD_SIGNAL, "user.name.*>=.*5"),
], ids=["retired-system", "unknown-user-attr", "unknown-source", "undeclared-env-key", "bare-signal-name", "string-vs-number"])
def test_a_trigger_referencing_anything_undeclared_or_mistyped_is_rejected_at_build_time(trigger, top_yaml, match):
    """`system.*` (today/time) is gone — a bare name outside every reserved
    namespace fails like any other unknown; a bare signal name must fail
    loudly, not silently resolve to nothing; user.name is a string, never a
    number, so comparing it with `>=` is caught statically."""
    with pytest.raises(ValueError, match=match):
        AutomatonBuilder().build({"index.yml": _project(trigger, top_yaml)})


def test_a_trigger_may_reference_a_declared_env_key_or_a_signal_against_a_type_consistent_threshold():
    assert _build("env.visits >= 1", "env:\n  visits: {}\n") == "env.visits >= 1"
    assert _build("signal.mood >= 75", MOOD_SIGNAL) == "signal.mood >= 75"
