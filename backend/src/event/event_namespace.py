from __future__ import annotations

import ast
from typing import Any

from automaton.automaton import Action, Automaton, State
from automaton.trigger_namespaces import TriggerNamespace
from system import bus
from system.bus import POINT_CORE_SERVICES
from system.web_session import WebSession

NAME = "event"


def _chains(trigger: str) -> list[tuple[str, ...]]:
    tree = ast.parse(trigger, mode="eval")
    nested = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    chains: list[tuple[str, ...]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or id(node) in nested:
            continue
        attrs = [node.attr]
        cur = node.value
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id == NAME:
            chains.append((cur.id, *reversed(attrs)))
    return chains


def project_refs(trigger: str) -> set[str]:
    return {chain[1] for chain in _chains(trigger) if len(chain) > 1}


def watched_from(automaton: Automaton, state_key: str) -> set[str]:
    state = automaton.states.get(state_key)
    if state is None:
        return set()
    refs: set[str] = set()
    for action in state.actions:
        if action.trigger and action.target == state.key:
            refs |= project_refs(action.trigger)
    return refs


class EventNamespace(TriggerNamespace):
    name = NAME

    def check_action(self, state: State, action: Action) -> None:
        if not action.trigger:
            return
        chains = _chains(action.trigger)
        if not chains:
            return
        context = f"State {state.key}, action '{action.name}': trigger"
        if action.target != state.key:
            raise ValueError(
                f"{context} references {NAME}.* but this action isn't a self-loop "
                f"(target '{action.target}' != state '{state.key}') — "
                f"{NAME}.* is only ever allowed in a self-loop action's own trigger."
            )
        for chain in chains:
            is_state = len(chain) == 3 and chain[2] == "state"
            is_env = len(chain) == 4 and chain[2] == "env"
            if not (is_state or is_env):
                raise ValueError(
                    f"{context} references {'.'.join(chain)} — {NAME}.<id> only has .state and .env.<key>."
                )

    def scope_for(self, automaton: Automaton) -> object:
        core = self._core()
        return _ScopedEventNamespace(core["db"], core["project_service"], automaton.family)

    def identifiers(self, automaton: Automaton) -> dict[str, dict[str, str]]:
        registry: dict[str, dict[str, str]] = {NAME: {}}
        if automaton.family is None:
            return registry
        core = self._core()
        db, project_service = core["db"], core["project_service"]
        for other_id in db.list_projects():
            if other_id == automaton.project_id:
                continue
            try:
                other = project_service.get_automaton(other_id, db.get_project_revision(other_id))
            except Exception:  # noqa: BLE001
                continue
            if other.family != automaton.family:
                continue
            registry[f"{NAME}.{other_id}"] = {"state": f"The '{other_id}' project's own current state."}
            registry[f"{NAME}.{other_id}.env"] = {
                env_key.name: env_key.ui_description or "" for env_key in other.env_keys
            }
        return registry

    @staticmethod
    def _core() -> dict:
        return bus.collect(POINT_CORE_SERVICES, {})


class _ScopedEventNamespace:
    def __init__(self, db, project_service, caller_family: str | None) -> None:
        self._db = db
        self._project_service = project_service
        self._caller_family = caller_family

    def __getattr__(self, project_id: str) -> "_ProjectProxy":
        if project_id.startswith("__"):
            raise AttributeError(project_id)
        return _ProjectProxy(self._db, self._project_service, WebSession().user, self._caller_family, project_id)


class _ProjectProxy:
    def __init__(self, db, project_service, username: str, caller_family: str | None, project_id: str) -> None:
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
            f"{NAME}.{self._project_id}: no project declares this as its own project.id.",
        )

    def _resolve(self) -> tuple[Any, Any, str] | None:
        if self._caller_family is None or not self._db.project_exists(self._project_id):
            self._not_found()
            return None
        try:
            resolved = self._project_service.get_automaton_and_state_for_observer(self._project_id, self._username)
        except FileNotFoundError:
            self._warn(self._project_id, "project_not_found", f"{NAME}.{self._project_id}: project does not exist.")
            return None
        if resolved is None:
            self._warn(
                self._project_id, "no_session",
                f"{NAME}.{self._project_id}: user '{self._username}' has no session in this project.",
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
                f"{NAME}.{self._parent._project_id}.env.{key}: not declared in that project's own "
                "'env' section.",
            )
            return None
        return self._parent._db.get_action_env(project_id, self._parent._username).get(key)
