"""Per-(user, project) "environment" memory. Two stores kept separate —
`stored()` (free-form, model-reported via [env]...[/env]) and
`action_set()` (deterministic, from an action's YAML `env:` field) — so
the Inspector Env tab can badge them apart and know which are editable.
`Env` is a plain in-memory store; `PersistedEnv` reads/writes through `db`."""
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
        stored: dict[str, Any] | None = None,
        action_set: dict[str, Any] | None = None,
    ) -> None:
        self._stored: dict[str, Any] = dict(stored or {})
        self._action_set: dict[str, Any] = dict(action_set or {})

    def _write_stored(self, values: dict[str, Any], message_id: int | None = None) -> None:
        self._stored = values

    def _write_action_set(self, values: dict[str, Any]) -> None:
        self._action_set = values

    def action_set(self) -> dict[str, Any]:
        """Just the persisted values an action's own YAML `env:` field set —
        kept separate from stored()'s model-reported values so the `env`
        evaluation-scope namespace can deliberately exclude free-form ones."""
        return dict(self._action_set)

    def update_action_set(self, values: dict[str, Any]) -> None:
        """action_set()'s own update — fired by an action, never the model
        itself (that's update(), for `[env]`-reported values). Merges
        onto whatever's already action-set."""
        if not values:
            return
        merged = {**self.action_set(), **values}
        self._write_action_set(merged)

    def stored(self) -> dict[str, Any]:
        """The persisted, free-form key:values — reported separately from
        action_set() so the Inspector Env tab knows which are actually
        editable/deletable (only these are)."""
        return dict(self._stored)

    def update(self, values: dict[str, Any], message_id: int | None = None) -> None:
        if not values:
            return
        action_set = self.action_set()
        filtered = {key: value for key, value in values.items() if key not in action_set}
        if not filtered:
            return
        merged = {**self.stored(), **filtered}
        self._write_stored(merged, message_id)

    def set_value(self, key: str, value: str) -> None:
        """Alias for update({key: value}), used by the Inspector Env
        tab's edit-in-place."""
        self.update({key: value})

    def delete_key(self, key: str) -> None:
        """Used by the Inspector Env tab's "delete this pair" action."""
        current = self.stored()
        if key not in current:
            return
        del current[key]
        self._write_stored(current)

    def clear(self) -> None:
        self._write_stored({})
        self._write_action_set({})

    def get(self, key: str, default: Any = None) -> Any:
        # action_set() takes priority on a name collision — an action's
        # own `env:` field is the more deliberate/authoritative source
        # than whatever the model itself reported under the same name.
        return {**self.stored(), **self.action_set()}.get(key, default)

    def serialise_as_text(self) -> str:
        """Every stored value plus every action-set one, merged into the
        text rendered back into the turn's [env]...[/env] block.
        System/session facts are never included here — those are
        evaluation-scope-only."""
        merged = {**self.stored(), **self.action_set()}
        return "\n".join(f"{key}: {value}" for key, value in merged.items())


class PersistedEnv(Env):
    """Production's own Env — reads/writes through `db` instead of the
    base class's in-memory dicts."""

    def __init__(self, db: Db, project_service: "ProjectService", username: str | None = None) -> None:
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
        another's Tracking rows."""
        super().__init__()
        self._db = db
        self._project_service = project_service
        self._username = username

    def _project_id(self) -> str:
        return self._project_service.get_active_project_id()

    def _user(self) -> str:
        return self._username if self._username is not None else Session().user

    def stored(self, until: datetime | None = None) -> dict[str, Any]:
        """`until` (naive-but-UTC): as they stood at or before that
        point, for the "Label sessions" view's point-in-time Inspector
        (see ChatService.get_env); omitted (None) means live/current."""
        return self._db.get_env(self._project_id(), self._user(), until=until)

    def action_set(self, until: datetime | None = None) -> dict[str, Any]:
        """Same `until` convention as stored()."""
        return self._db.get_action_env(self._project_id(), self._user(), until=until)

    def _write_stored(self, values: dict[str, Any], message_id: int | None = None) -> None:
        self._db.set_env(self._project_id(), values, self._user(), message_id=message_id)

    def _write_action_set(self, values: dict[str, Any]) -> None:
        self._db.set_action_env(self._project_id(), values, self._user())
