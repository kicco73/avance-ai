"""Loads a project's automaton from a pre-compiled package (see
backend/bin/compile_automaton.py) instead of building it from Archive rows
via AutomatonBuilder — selected by `project-service.compiled-automaton` in
.config.yml (see config.py, AppConfig.compiled_automaton_module); None
there (the default) keeps ProjectService on the generic AutomatonLoader.

FIRST CUT, deliberately narrow: this only proves the config ->
ProjectService plumbing that picks this loader instead of AutomatonLoader.
The object load()/load_at_revision() return is the compiled module's own
AUTOMATON singleton, which does NOT share Automaton's interface — no
eval_action_env/eval_action_on_exit/evaluate_triggers_action/
get_state_payload, states as attributes rather than a `states` dict,
actions as methods rather than `Action` dataclass instances, one call
(`evaluate_triggers`/`apply_manual_action`) that does on-exit and task-
posting itself and returns only the next state (see compile_automaton.py's
own module docstring for the full list of what's compiled away). Every
caller downstream of ProjectService that assumes the generic Automaton
shape — ProjectInspector, chat_service, TrackingEngine, the design-view
editor, metrics — is expected to break until it's taught to recognize a
compiled automaton and take a different path. That reconciliation is
separate, not-yet-started work; this class is not a full AutomatonLoader
replacement yet, just the switch that decides which one ProjectService
builds.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from db import Db

from .automaton_loader import AutomatonLoader

if TYPE_CHECKING:
    # Type-only, same reason as AutomatonLoader's own TYPE_CHECKING import.
    from chat.sessions.session_manager import ChatSessionManager


class CompiledAutomatonLoader:
    """Same public method names as AutomatonLoader, backed by one
    pre-compiled package instead of Db-stored Archive rows. A compiled
    package has no revisions and no cross-project family scan — every
    method that exists only to serve those concepts here is a no-op or a
    fixed answer, not a real cache: there is exactly one automaton, for
    exactly one project, chosen once at construction time."""

    def __init__(self, db: Db, module_name: str, session_manager: "ChatSessionManager | None" = None) -> None:
        self._db = db
        # Unused today (no cache to invalidate, no session to force-close
        # on a broken revision — a compiled module either imports or it
        # doesn't) — kept only so this constructor accepts the same
        # keyword ProjectService already passes to AutomatonLoader.
        self._session_manager = session_manager
        self._module_name = module_name
        self._automaton = self._import_automaton(module_name)

    @staticmethod
    def _import_automaton(module_name: str) -> Any:
        module = importlib.import_module(module_name)
        automaton = getattr(module, "AUTOMATON", None)
        if automaton is None:
            raise ImportError(f"Compiled automaton module {module_name!r} has no AUTOMATON attribute.")
        return automaton

    # Pure path-safety logic, nothing to do with how the automaton is
    # built — reused as-is rather than duplicated.
    is_safe_project_name = staticmethod(AutomatonLoader.is_safe_project_name)

    def known_projects_env_keys(self, project_id: str, family: str | None) -> dict[str, frozenset[str]]:
        # Cross-project automaton.<id> references aren't supported for a
        # compiled package yet — it only ever knows about itself.
        return {}

    def declared_family(self, project_id: str) -> str | None:
        return None

    def invalidate_cache(self, project_id: str) -> None:
        pass

    def clear_all_build_failures(self) -> None:
        pass

    def invalidate(self, project_id: str, revision: int) -> None:
        pass

    def set_cached(self, project_id: str, revision: int, automaton: Any) -> None:
        pass

    def load_at_revision(self, project_id: str, revision: int) -> Any:
        return self._automaton

    def load(self, project_id: str) -> Any:
        return self._automaton
