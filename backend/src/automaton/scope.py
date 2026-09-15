"""The names an automaton's expressions evaluate against."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from automaton.automaton import Automaton


class EvaluationScope(dict):
    """The `names` dict a trigger/env/task expression evaluates
    against (see tracking.evaluation_scope.EvaluationScopeBuilder — the
    one place it is assembled), remembering *where it was built from*:
    the automaton and state key its expressions belong to. Still a plain
    dict to simpleeval; the attributes exist for whoever must
    reconstruct an equivalent scope later — a task.defer'd call
    outliving the process it was evaluated in (see tracking/actuators/).

    for_task() is the view a task line sees: the same names
    minus IdentifierRegistry.TASK_SCOPE_EXCLUDES, so `session`/`chat` are
    simply absent there rather than forbidden by a check."""

    def __init__(
        self, names: dict[str, Any], *, automaton: "Automaton", state_key: str, action_name: str | None = None,
    ) -> None:
        super().__init__(names)
        self.automaton = automaton
        self.state_key = state_key
        self.action_name = action_name

    def for_on_exit(self, action_name: str | None = None) -> "EvaluationScope":
        """The view an on-exit script runs in: the same names, in a copy
        of its own, so the locals the script assigns live there and die
        with it — never in the scope the caller goes on to hand to task."""
        return EvaluationScope(self, automaton=self.automaton, state_key=self.state_key, action_name=action_name)

    def for_task(self, action_name: str | None = None) -> "EvaluationScope":
        from automaton.identifier_registry import IdentifierRegistry
        names = IdentifierRegistry.excluding(self, IdentifierRegistry.TASK_SCOPE_EXCLUDES)
        return EvaluationScope(names, automaton=self.automaton, state_key=self.state_key, action_name=action_name)
