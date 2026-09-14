from __future__ import annotations

from typing import TYPE_CHECKING

from system import bus
from system.bus import POINT_TRIGGER_NAMESPACES

if TYPE_CHECKING:
    from automaton.automaton import Action, Automaton, State


class TriggerNamespace:
    name: str = ""

    def check_action(self, state: "State", action: "Action") -> None:
        raise NotImplementedError

    def scope_for(self, automaton: "Automaton") -> object:
        raise NotImplementedError

    def identifiers(self, automaton: "Automaton") -> dict[str, dict[str, str]]:
        raise NotImplementedError


class TriggerNamespaces:
    def __init__(self) -> None:
        self._declared: dict[str, TriggerNamespace] = {}

    @classmethod
    def collect(cls) -> "TriggerNamespaces":
        return bus.collect(POINT_TRIGGER_NAMESPACES, cls())

    def declare(self, namespace: TriggerNamespace) -> None:
        self._declared[namespace.name] = namespace

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._declared)

    def check_action(self, state: "State", action: "Action") -> None:
        for namespace in self._declared.values():
            namespace.check_action(state, action)

    def scope(self, automaton: "Automaton") -> dict[str, object]:
        return {name: namespace.scope_for(automaton) for name, namespace in self._declared.items()}

    def identifiers(self, automaton: "Automaton") -> dict[str, dict[str, str]]:
        merged: dict[str, dict[str, str]] = {}
        for namespace in self._declared.values():
            merged.update(namespace.identifiers(automaton))
        return merged
