"""Duck-types just enough of ProjectService's interface
(get_active_automaton/get_active_automaton_and_state/get_active_project_id) for Signals/
PersistedEnv/SessionFacts/MetricService to resolve a
FIXED automaton/project pair instead of whatever's live right now.

Needed anywhere a caller must stay pinned to a specific, non-active
context: a test replaying an old revision (test/
signal_sources.py, test/testing_service.py), a live
turn's own already-resolved session automaton (tracking/
tracking_processor.py), or a listener re-evaluating some other user's
project outside any request of theirs. None of
these may silently read the live active-project pointer instead — it
could name a completely different project by the time these classes
actually call it.
"""
from __future__ import annotations

from typing import Protocol

from automaton.automaton import Automaton, State


class ProjectContext(Protocol):
    """The slice of ProjectService this module's own docstring promises
    to duck-type — every consumer that only ever needs a fixed
    automaton/project pair (never the live active one) should type its
    own `project_service`-shaped parameter against this, not the
    concrete ProjectService, so a FixedProjectContext satisfies it."""

    def get_active_automaton(self) -> Automaton | None: ...
    def get_active_automaton_and_state(self) -> tuple[Automaton | None, State | None]: ...
    def get_active_project_id(self) -> str | None: ...


class FixedProjectContext:
    """Either argument may be omitted when a caller only needs the
    other (e.g. Signals never reads project_id; PersistedEnv/
    SessionFacts/MetricService never read the automaton)."""

    def __init__(self, automaton: Automaton | None = None, project_id: str | None = None) -> None:
        self._automaton = automaton
        self._project_id = project_id

    def get_active_automaton(self):
        return self._automaton

    def get_active_automaton_and_state(self):
        return self._automaton, None

    def get_active_project_id(self) -> str | None:
        return self._project_id
