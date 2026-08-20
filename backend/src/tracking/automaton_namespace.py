"""The `automaton` scope namespace (see tracking.evaluation_scope.
EvaluationScopeBuilder) — automaton.<project>.state and automaton.
<project>.env.<key>, resolving a DIFFERENT project's own live current
state / declared-env action-set value, for the SAME user. Self-loop-only
in a trigger is enforced at build time (see automaton_builder.py's own
check against automaton.automaton.trigger_automaton_project_refs) — this
class itself never enforces that, it only ever resolves whatever
attribute chain it's asked to.

Every failure mode resolves to None and records a SystemWarning instead
of raising — a broken/misconfigured cross-project reference must never
crash the *referencing* project's own turn:
  - 'project_not_found': <project> doesn't exist (or was deleted).
  - 'no_session': the current user has never talked to <project> at all
    (see ProjectService.get_automaton_and_state_for_observer).
  - 'env_key_not_declared': <key> isn't in <project>'s own declared
    `env:` section (see automaton.automaton.EnvKey/Prompt 5).
"""
from __future__ import annotations

from typing import Any, Callable

from db.db import Db
from project.project_service import ProjectService

GetUsername = Callable[[], str]


class AutomatonNamespace:
    """`get_username`: a callable, not a plain string — same "always
    read fresh, never staled off a value captured at construction time"
    convention tracking.session_facts.SessionFacts/tracking.env.
    PersistedEnv already use for the exact same reason (this is
    constructed once, long-lived, by ChatService/TrackingService.process
    alongside those)."""

    def __init__(self, db: Db, project_service: ProjectService, get_username: GetUsername) -> None:
        self._db = db
        self._project_service = project_service
        self._get_username = get_username

    def __getattr__(self, project_name: str) -> "_ProjectProxy":
        if project_name.startswith("__"):
            raise AttributeError(project_name)
        return _ProjectProxy(self._db, self._project_service, self._get_username(), project_name)


class _ProjectProxy:
    def __init__(self, db: Db, project_service: ProjectService, username: str, project_name: str) -> None:
        self._db = db
        self._project_service = project_service
        self._username = username
        self._project_name = project_name

    def _warn(self, kind: str, message: str) -> None:
        self._db.save_system_warning(self._username, self._project_name, kind, message)

    def _resolve(self) -> tuple[Any, Any] | None:
        """(automaton, state) for this project, as seen by self._username
        right now — None (after recording a SystemWarning) for either of
        the two runtime-only failure modes this class is responsible
        for; a build-time-impossible third one (a malformed project
        name) would surface as some other exception entirely, never
        caught here."""
        try:
            resolved = self._project_service.get_automaton_and_state_for_observer(
                self._project_name, self._username
            )
        except FileNotFoundError:
            self._warn("project_not_found", f"automaton.{self._project_name}: project does not exist.")
            return None
        if resolved is None:
            self._warn(
                "no_session",
                f"automaton.{self._project_name}: user '{self._username}' has no session in this project.",
            )
            return None
        return resolved

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
        automaton, _ = resolved
        if key not in {env_key.name for env_key in automaton.env_keys}:
            self._parent._warn(
                "env_key_not_declared",
                f"automaton.{self._parent._project_name}.env.{key}: not declared in that project's own "
                "'env' section.",
            )
            return None
        return self._parent._db.get_action_env(self._parent._project_name, self._parent._username).get(key)
