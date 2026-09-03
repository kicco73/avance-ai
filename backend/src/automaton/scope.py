"""The names an automaton's expressions evaluate against."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from automaton.automaton import Automaton


class EvaluationScope(dict):
    """The `names` dict a trigger/env/on-enter expression evaluates
    against (see tracking.evaluation_scope.EvaluationScopeBuilder — the
    one place it is assembled), remembering *where it was built from*:
    the automaton and state key its expressions belong to. Still a plain
    dict to simpleeval; the attributes exist for whoever must
    reconstruct an equivalent scope later — an actuator.defer'd call
    outliving the process it was evaluated in (see tracking/actuators/).

    for_actuators() is the view an on-enter line sees: the same names
    minus IdentifierRegistry.ACTUATOR_SCOPE_EXCLUDES, so `session` is
    simply absent there rather than forbidden by a check."""

    def __init__(
        self, names: dict[str, Any], *, automaton: "Automaton", state_key: str, action_name: str | None = None,
    ) -> None:
        super().__init__(names)
        self.automaton = automaton
        self.state_key = state_key
        # Only set on the actuator view: the action whose on-enter is being rendered.
        self.action_name = action_name

    def for_actuators(self, action_name: str | None = None) -> "EvaluationScope":
        # Imported here: identifier_registry imports automaton.automaton,
        # which imports this module — a top-level import would be circular.
        from automaton.identifier_registry import IdentifierRegistry
        names = IdentifierRegistry.excluding(self, IdentifierRegistry.ACTUATOR_SCOPE_EXCLUDES)
        return EvaluationScope(names, automaton=self.automaton, state_key=self.state_key, action_name=action_name)
