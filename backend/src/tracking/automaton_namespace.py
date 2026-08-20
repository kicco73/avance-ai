"""The `automaton` scope namespace (see tracking.evaluation_scope.
EvaluationScopeBuilder) — automaton.<project>.state and automaton.
<project>.env.<key> resolve a DIFFERENT project's live state / declared
env value, for the SAME user. `<project>` is a project_id, never the raw
project_name (which isn't guaranteed to be a valid Python identifier).

Every failure mode resolves to None and records a SystemWarning instead
of raising, since a broken cross-project reference must never crash the
referencing project's own turn: 'project_not_found' (no project declares
this project_id), 'no_session' (user never talked to that project), or
'env_key_not_declared' (key not in that project's own `env:` section).
"""
from __future__ import annotations

from typing import Any, Callable

from db.db import Db
from project.project_service import ProjectService

GetUsername = Callable[[], str]


class AutomatonNamespace:
    """`get_username` is a callable, not a plain string, so it's read
    fresh each time rather than staled off the value at construction —
    this object is constructed once and kept long-lived."""

    def __init__(self, db: Db, project_service: ProjectService, get_username: GetUsername) -> None:
        self._db = db
        self._project_service = project_service
        self._get_username = get_username

    def __getattr__(self, project_id: str) -> "_ProjectProxy":
        if project_id.startswith("__"):
            raise AttributeError(project_id)
        return _ProjectProxy(self._db, self._project_service, self._get_username(), project_id)


class _ProjectProxy:
    def __init__(self, db: Db, project_service: ProjectService, username: str, project_id: str) -> None:
        self._db = db
        self._project_service = project_service
        self._username = username
        self._project_id = project_id

    def _warn(self, project_name: str, kind: str, message: str) -> None:
        self._db.save_system_warning(self._username, project_name, kind, message)

    def _resolve(self) -> tuple[Any, Any, str] | None:
        """(automaton, state, project_name) for this project, or None if
        resolution fails at any step — a SystemWarning is recorded in
        that case instead of raising, since this must never crash the
        caller."""
        project_name = self._db.get_project_name_by_project_id(self._project_id)
        if project_name is None:
            self._warn(
                self._project_id, "project_not_found",
                f"automaton.{self._project_id}: no project declares this as its own project.id.",
            )
            return None
        try:
            resolved = self._project_service.get_automaton_and_state_for_observer(project_name, self._username)
        except FileNotFoundError:
            self._warn(project_name, "project_not_found", f"automaton.{self._project_id}: project does not exist.")
            return None
        if resolved is None:
            self._warn(
                project_name, "no_session",
                f"automaton.{self._project_id}: user '{self._username}' has no session in this project.",
            )
            return None
        automaton, state = resolved
        return automaton, state, project_name

    @property
    def state(self) -> str | None:
        resolved = self._resolve()
        return resolved[1].key if resolved is not None else None

    @property
    def env(self) -> "_ProjectEnvProxy":
        return _ProjectEnvProxy(self)


class _ProjectEnvProxy:
    def __init__(self, parent: _ProjectProxy) -> None:
        self._parent = parent

    def __getattr__(self, key: str) -> Any:
        if key.startswith("__"):
            raise AttributeError(key)
        resolved = self._parent._resolve()
        if resolved is None:
            return None
        automaton, _, project_name = resolved
        if key not in {env_key.name for env_key in automaton.env_keys}:
            self._parent._warn(
                project_name, "env_key_not_declared",
                f"automaton.{self._parent._project_id}.env.{key}: not declared in that project's own "
                "'env' section.",
            )
            return None
        return self._parent._db.get_action_env(project_name, self._parent._username).get(key)
