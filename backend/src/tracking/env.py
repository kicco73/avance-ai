"""Per-(user, project) "environment" memory. Two stores kept separate —
`stored()` (free-form, model-reported via [env]...[/env]) and
`action_set()` (deterministic, from an action's YAML `env:` field) — so
the Inspector Env tab can badge them apart and know which are editable.
`Env` is a plain in-memory store; `PersistedEnv` reads/writes through `db`."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from db import Db

GetUsername = Callable[[], str]
GetActiveProjectName = Callable[[], str]


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
        """Wipes every stored (free-form) key. Action-set values live in
        a separate store and are untouched by this — see clear_action_set."""
        self._write_stored({})

    def clear_action_set(self) -> None:
        """clear()'s equivalent for action-set values. An action whose
        `env:` field still fires will simply re-populate what it sets on
        its next turn."""
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

    def __init__(self, db: Db, get_username: GetUsername, get_active_project_name: GetActiveProjectName) -> None:
        super().__init__()
        self._db = db
        self._get_username = get_username
        self._get_active_project_name = get_active_project_name

    def stored(self, until: datetime | None = None) -> dict[str, Any]:
        """`until` (naive-but-UTC): as they stood at or before that
        point, for the "Label sessions" view's point-in-time Inspector
        (see ChatService.get_env); omitted (None) means live/current."""
        return self._db.get_env(self._get_active_project_name(), self._get_username(), until=until)

    def action_set(self, until: datetime | None = None) -> dict[str, Any]:
        """Same `until` convention as stored()."""
        return self._db.get_action_env(self._get_active_project_name(), self._get_username(), until=until)

    def _write_stored(self, values: dict[str, Any], message_id: int | None = None) -> None:
        self._db.set_env(self._get_active_project_name(), values, self._get_username(), message_id=message_id)

    def _write_action_set(self, values: dict[str, Any]) -> None:
        self._db.set_action_env(self._get_active_project_name(), values, self._get_username())
