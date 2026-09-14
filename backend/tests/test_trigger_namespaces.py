"""The trigger.namespaces contribution point: a root name nobody declared
is an undefined name, a declared one is accepted and asked to check every
action, and what it declines refuses the build.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.trigger_namespaces import TriggerNamespace
from system import bus
from system.bus import POINT_TRIGGER_NAMESPACES

pytestmark = pytest.mark.contract

YML = """
project:
  id: test_project
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: a
        trigger: "{trigger}"
"""


class _Strict(TriggerNamespace):
    name = "strict"

    def __init__(self) -> None:
        self.checked: list[str] = []

    def check_action(self, state, action) -> None:
        self.checked.append(action.name)
        if "forbidden" in (action.trigger or ""):
            raise ValueError("strict.forbidden is not allowed here")

    def scope_for(self, automaton) -> object:
        return {}

    def identifiers(self, automaton) -> dict[str, dict[str, str]]:
        return {"strict": {}}


def _build(trigger: str):
    return AutomatonBuilder().build({"index.yml": YML.format(trigger=trigger)})


def test_an_undeclared_root_name_is_an_undefined_name():
    with pytest.raises(ValueError, match="undefined name.*strict"):
        _build("strict.thing == 1")


def test_a_declared_namespace_is_accepted_and_gets_to_check_every_action():
    strict = _Strict()
    bus.contribute(POINT_TRIGGER_NAMESPACES, lambda namespaces: namespaces.declare(strict))

    automaton = _build("strict.thing == 1")

    assert automaton.states["a"].actions[0].trigger == "strict.thing == 1"
    assert strict.checked == ["go"]


def test_what_a_declared_namespace_refuses_refuses_the_build():
    bus.contribute(POINT_TRIGGER_NAMESPACES, lambda namespaces: namespaces.declare(_Strict()))

    with pytest.raises(ValueError, match="strict.forbidden is not allowed here"):
        _build("strict.forbidden == 1")
