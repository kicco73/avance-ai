"""Per-(user, project) "environment" memory — free-form key:value facts
the model can extend at will via [env]...[/env] (see chat.
metadata_handler.MetadataHandler), persisted as a dedicated env-only row
on the db.py Tracking event log (see db.Db.get_env/set_env, and
Tracking's own docstring) — every project a user has ever talked to
keeps its own independent one, just like an automaton instance's own
live state. Scoped through the same session -> ChatSession relationship
as the rest of Tracking: a "Reset conversation" or project deletion
wipes it right along with everything else, exactly like any other
Tracking row for that session. Instantiated as ChatService's `env`, same
DI style as tracking/definitions.py's Signals and metrics/metric_
service.py's MetricService.

Two stores only — `stored()` (free-form, model-reported via [env]) and
`action_set()` (deterministic, set by an action's own YAML `env:` field)
— kept separate so the Inspector Env tab can badge the two apart ("AI"
vs "ACTION") and know which are actually editable/deletable (only the
stored ones, see set_value/delete_key). The "always computed fresh"
facts this class used to also carry (today/time/session duration/...,
see ENV_COMPUTED_KEYS) now live in their own dedicated classes, entirely
outside Env — see tracking.system_facts.SystemFacts/tracking.
session_facts.SessionFacts and tracking.evaluation_scope.
EvaluationScopeBuilder, the one place all four (signal/env/system/
session) get assembled into one evaluation scope.

`Env` itself is a plain in-memory store — a benchmark replay (not
introduced here) can use it directly, updating that internal state once
per turn as it orchestrates its own loop. `PersistedEnv` is production's
own subclass, reading/writing through `db` instead.
"""
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
        """Just the persisted values an action's own YAML `env:` field set
        (see automaton_builder.py's _build_action/Automaton.
        eval_action_env, and update_action_set below) — kept in a
        separate store from `stored()`'s model-reported ones so the
        Inspector Env tab can badge the two apart ("SET" vs "AI"), even
        though both are merged together into what actually reaches the
        turn's own prompt (see serialise_as_text) — and so
        EvaluationScopeBuilder can populate the `env` namespace with
        *only* this, deliberately excluding stored()'s free-form values
        (see that class's own docstring for why)."""
        return dict(self._action_set)

    def update_action_set(self, values: dict[str, Any]) -> None:
        """action_set()'s own update — an action firing (see
        chat_service.py's/tracking_engine.py's own apply_action_env),
        never the model itself (that's update(), for `[env]`-reported
        values). Merges onto whatever's already action-set, same
        no-op-for-falsy rule as update()."""
        if not values:
            return
        merged = {**self.action_set(), **values}
        self._write_action_set(merged)

    def stored(self) -> dict[str, Any]:
        """Just the persisted, free-form key:values. Public — unlike the
        rest of this class's read path, the Inspector's own Env tab needs
        stored and action-set values reported separately so it knows
        which are actually editable/deletable (see set_value/delete_key:
        only these are)."""
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
        """The Inspector Env tab's own "click a value to edit it" — a
        thin, explicit alias for update({key: value})."""
        self.update({key: value})

    def delete_key(self, key: str) -> None:
        """The Inspector Env tab's own "delete this pair" — a no-op if
        `key` isn't currently stored (nothing to remove)."""
        current = self.stored()
        if key not in current:
            return
        del current[key]
        self._write_stored(current)

    def clear(self) -> None:
        """The Inspector Env tab's own "clear all" for the AI section —
        wipes every stored key at once. Action-set ones (see action_set/
        clear_action_set) live in a separate store, untouched by this."""
        self._write_stored({})

    def clear_action_set(self) -> None:
        """The Inspector Env tab's own "clear all" for the ACTION
        section — action_set()'s own equivalent of clear() above. An
        action whose `env:` field still fires afterward will simply
        re-populate whatever it sets again on its next turn, same as any
        other action-set write (see update_action_set)."""
        self._write_action_set({})

    def get(self, key: str, default: Any = None) -> Any:
        # action_set() takes priority on a name collision — an action's
        # own `env:` field is the more deliberate/authoritative source
        # for a key it manages, vs. whatever the model itself happened
        # to report under the same name (see stored/action_set's own
        # docstrings; collisions aren't expected by design, but this
        # keeps precedence well-defined if one ever occurs).
        return {**self.stored(), **self.action_set()}.get(key, default)

    def serialise_as_text(self) -> str:
        """Every stored value plus every action-set one, freshly
        assembled — what MetadataHandler.build_prompt renders back into
        the turn's own [env]...[/env] block. No system/session facts
        here anymore (see this module's own docstring) — those are the
        `system`/`session` namespaces now, evaluation-scope-only, never
        rendered into the prompt."""
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
