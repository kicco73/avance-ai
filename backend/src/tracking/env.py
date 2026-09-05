"""Per-(user, project) "environment" store. Two stores kept separate under
one object, with two different owners: `memory()` — the model's own
free-form notes, written only by the model (the `memory` field of its
structured reply) and read only by the model (the prompt's own "Current
memory" block); no script or trigger ever sees it — and `action_set()` —
the automaton's declared env keys, deterministic, written by an action's
own YAML `env:` field (or, for a readwrite key, by the model through an
`avance:env` source's `update`, see tracking.sources.avance_env) and read
by triggers, scripts and the prompt's own env block alike. `Env` is a
plain in-memory store; `PersistedEnv` reads/writes through `db`."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from db import Db
from session import Session

if TYPE_CHECKING:
    # Deferred: project.project_service imports tracking.tracking_engine,
    # which imports Env from this very module — a real top-level import
    # here would be circular. Safe as a type-only import since `from
    # __future__ import annotations` (above) never evaluates it at runtime.
    from project.project_service import ProjectService


class Env(object):
    def __init__(
        self,
        memory: dict[str, Any] | None = None,
        action_set: dict[str, Any] | None = None,
    ) -> None:
        self._memory: dict[str, Any] = dict(memory or {})
        self._action_set: dict[str, Any] = dict(action_set or {})

    def _write_memory(self, values: dict[str, Any], message_id: int | None = None) -> None:
        self._memory = values

    def _write_action_set(self, values: dict[str, Any], origin: str | None = None) -> int | None:
        self._action_set = values
        return None

    def action_set(self, until: datetime | None = None) -> dict[str, Any]:
        """Just the persisted values an action's own YAML `env:` field (or
        the model's own `update` on an avance:env source) set — kept
        separate from memory()'s model-reported notes so the `env`
        evaluation-scope namespace can deliberately exclude those."""
        return dict(self._action_set)

    def update_action_set(self, values: dict[str, Any], origin: str | None = None) -> int | None:
        """action_set()'s own update — fired by an action's `env:`, or by
        the model's `update` on an avance:env source (origin "tool", so
        TrackingProcessor can later bind that write to the turn's own
        assistant message — see Db.link_tool_env_writes_to_message);
        never the reply's own memory field (that's update()). Merges onto
        whatever's already action-set. Returns the Tracking row id the
        write landed in, None for an in-memory store."""
        if not values:
            return None
        merged = {**self.action_set(), **values}
        return self._write_action_set(merged, origin)

    def memory(self, until: datetime | None = None) -> dict[str, Any]:
        """The model's own persisted, free-form notes — reported separately
        from action_set() so the Inspector knows which are actually
        editable/deletable (only these are)."""
        return dict(self._memory)

    def update(self, values: dict[str, Any], message_id: int | None = None, declared_keys: set[str] | None = None) -> None:
        """Merges the reply's own `memory` delta onto memory(). A key the
        automaton *declares* (`declared_keys`, e.g. Automaton.
        declared_env_key_names() — Env itself never imports the automaton)
        is dropped, never duplicated into memory, whether or not that key
        has actually been set yet — the model is told to change those only
        through the `update` tool, and this is the backstop. `declared_keys`
        omitted (no automaton in scope) means nothing is filtered."""
        if not values:
            return
        filtered = {key: value for key, value in values.items() if key not in (declared_keys or set())}
        if not filtered:
            return
        merged = {**self.memory(), **filtered}
        self._write_memory(merged, message_id)

    def set_value(self, key: str, value: str) -> None:
        """Alias for update({key: value}), used by the Inspector's own
        Memory section edit-in-place."""
        self.update({key: value})

    def delete_key(self, key: str) -> None:
        """Used by the Inspector's own "delete this pair" action."""
        current = self.memory()
        if key not in current:
            return
        del current[key]
        self._write_memory(current)

    def drop_action_set_keys(self, keys: set[str]) -> None:
        current = self.action_set()
        remaining = {key: value for key, value in current.items() if key not in keys}
        if remaining == current:
            return
        self._write_action_set(remaining)

    def clear(self) -> None:
        self._write_memory({})
        self._write_action_set({})

    def get(self, key: str, default: Any = None) -> Any:
        # action_set() takes priority on a name collision — an action's
        # own `env:` field is the more deliberate/authoritative source
        # than whatever the model itself noted under the same name.
        return {**self.memory(), **self.action_set()}.get(key, default)

    def memory_as_text(self) -> str:
        """memory() rendered as the "key: value" lines of the prompt's own
        "Current memory" block — memory only, never the automaton's env
        (that's tracking.env_prompt_block's job, with its own perimeter)."""
        return "\n".join(f"{key}: {value}" for key, value in self.memory().items())


class PersistedEnv(Env):
    """Production's own Env — reads/writes through `db` instead of the
    base class's in-memory dicts."""

    def __init__(
        self, db: Db, project_service: "ProjectService", session_id: int | None,
        username: str | None = None,
    ) -> None:
        """`project_service`: whatever answers get_active_project_id() —
        the real ProjectService (the request user's active project) or a
        FixedProjectContext pinned to one project. `username`: whose env
        this is; omitted, the request user (Session().user). Both must be
        pinned to the *session's own* project and user whenever the
        session being operated on isn't necessarily the request user's
        active-project session — a supervisor opening someone else's
        session, or any session of a non-active project (see
        ChatService._env_for_session): keyed on the active project, an
        env read here answers for one project and a write lands in
        another's Tracking rows.

        `session_id` has no default on purpose — _write_memory/
        _write_action_set below persist through it (Tracking.session is a
        real FK), so a caller with no real session must use a plain Env()
        instead (see ChatService._schedule_on_enter/tracking.actuators.
        on_enter_task.ScopeHydrator, both of which branch on session_id
        before ever constructing one of these) rather than pass None here
        implicitly and violate that FK on the first write."""
        super().__init__()
        self._db = db
        self._project_service = project_service
        self._session_id = session_id
        self._username = username

    def _project_id(self) -> str:
        return self._project_service.get_active_project_id()

    def _user(self) -> str:
        return self._username if self._username is not None else Session().user

    def memory(self, until: datetime | None = None) -> dict[str, Any]:
        """`until` (naive-but-UTC): as they stood at or before that
        point, for the "Label sessions" view's point-in-time Inspector
        (see ChatService.get_env); omitted (None) means live/current."""
        return self._db.get_env(self._project_id(), self._user(), until=until)

    def action_set(self, until: datetime | None = None) -> dict[str, Any]:
        """Same `until` convention as memory()."""
        return self._db.get_action_env(self._project_id(), self._user(), until=until)

    def _write_memory(self, values: dict[str, Any], message_id: int | None = None) -> None:
        self._db.set_env(self._session_id, values, message_id=message_id)

    def _write_action_set(self, values: dict[str, Any], origin: str | None = None) -> int | None:
        return self._db.set_action_env(self._session_id, values, origin=origin)
