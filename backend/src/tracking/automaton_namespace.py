"""The `automaton` scope namespace: automaton.<project>.state/env.<key>
resolve a DIFFERENT project's live state/env for the SAME user, only
within the calling project's own declared family (project.family, plain
string equality, never parsed — see automaton_builder.py's own
docstring) — a cross-family or family-less id resolves exactly like an
unknown one, so another family is never distinguishable from "doesn't
exist" (see scoped_to below). Failures resolve to None + SystemWarning
rather than raising, so a broken reference never crashes the caller's turn."""
from __future__ import annotations

from typing import Any

from db.db import Db
from project.project_service import ProjectService
from session import Session


class AutomatonNamespace:
    """Long-lived and shared across every evaluation — scoped_to binds it
    to one particular calling project's own family right before use (see
    tracking.evaluation_scope.EvaluationScopeBuilder.build), since family
    membership depends on who's asking, not on this object itself."""

    def __init__(self, db: Db, project_service: ProjectService) -> None:
        self._db = db
        self._project_service = project_service

    def scoped_to(self, caller_family: str | None) -> "_ScopedAutomatonNamespace":
        return _ScopedAutomatonNamespace(self._db, self._project_service, caller_family)


class _ScopedAutomatonNamespace:
    def __init__(self, db: Db, project_service: ProjectService, caller_family: str | None) -> None:
        self._db = db
        self._project_service = project_service
        self._caller_family = caller_family

    def __getattr__(self, project_id: str) -> "_ProjectProxy":
        if project_id.startswith("__"):
            raise AttributeError(project_id)
        return _ProjectProxy(self._db, self._project_service, Session().user, self._caller_family, project_id)


class _ProjectProxy:
    def __init__(self, db: Db, project_service: ProjectService, username: str, caller_family: str | None, project_id: str) -> None:
        self._db = db
        self._project_service = project_service
        self._username = username
        self._caller_family = caller_family
        self._project_id = project_id

    def _warn(self, project_id: str, kind: str, message: str) -> None:
        self._db.save_system_warning(self._username, project_id, kind, message)

    def _not_found(self) -> None:
        self._warn(
            self._project_id, "project_not_found",
            f"automaton.{self._project_id}: no project declares this as its own project.id.",
        )

    def _resolve(self) -> tuple[Any, Any, str] | None:
        """(automaton, state, project_id) for this project, or None if
        resolution fails at any step — a SystemWarning is recorded in
        that case instead of raising, since this must never crash the
        caller. A caller with no family at all, or a target outside the
        caller's own family, is reported the exact same "project_not_found"
        way as one that plain doesn't exist — see this module's own docstring."""
        if self._caller_family is None or not self._db.project_exists(self._project_id):
            self._not_found()
            return None
        try:
            resolved = self._project_service.get_automaton_and_state_for_observer(self._project_id, self._username)
        except FileNotFoundError:
            self._warn(self._project_id, "project_not_found", f"automaton.{self._project_id}: project does not exist.")
            return None
        if resolved is None:
            self._warn(
                self._project_id, "no_session",
                f"automaton.{self._project_id}: user '{self._username}' has no session in this project.",
            )
            return None
        automaton, state = resolved
        if automaton.family != self._caller_family:
            self._not_found()
            return None
        return automaton, state, self._project_id

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
        automaton, _, project_id = resolved
        if key not in {env_key.name for env_key in automaton.env_keys}:
            self._parent._warn(
                project_id, "env_key_not_declared",
                f"automaton.{self._parent._project_id}.env.{key}: not declared in that project's own "
                "'env' section.",
            )
            return None
        return self._parent._db.get_action_env(project_id, self._parent._username).get(key)
