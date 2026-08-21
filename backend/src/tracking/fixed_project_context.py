"""Duck-types just enough of ProjectService's interface
(get_active_automaton_and_state/get_active_project_name) for Signals/
PersistedEnv/SessionFacts/MetricService/AutomatonNamespace to resolve a
FIXED automaton/project pair instead of whatever's live right now.

Needed anywhere a caller must stay pinned to a specific, non-active
context: a benchmark replaying an old revision (metrics/
benchmark_signal_sources.py, metrics/benchmark_run_service.py), a live
turn's own already-resolved session automaton (tracking/
tracking_processor.py), or a cross-project wake-up re-evaluating some
other user's observer project (tracking/wakeup_service.py). None of
these may silently read the live active-project pointer instead — it
could name a completely different project by the time these classes
actually call it.
"""
from __future__ import annotations

from automaton.automaton import Automaton


class FixedProjectContext:
    """Either argument may be omitted when a caller only needs the
    other (e.g. Signals never reads project_name; PersistedEnv/
    SessionFacts/MetricService never read the automaton)."""

    def __init__(self, automaton: Automaton | None = None, project_name: str | None = None) -> None:
        self._automaton = automaton
        self._project_name = project_name

    def get_active_automaton_and_state(self):
        return self._automaton, None

    def get_active_project_name(self) -> str | None:
        return self._project_name
