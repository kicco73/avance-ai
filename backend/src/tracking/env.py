"""Per-(user, project) "environment" memory — free-form key:value facts
the model can extend at will via [env]...[/env] (see chat.
metadata_handler.MetadataHandler), persisted as a dedicated env-only row
on the db.py Tracking event log (see db.Db.get_env/set_env, and
Tracking's own docstring) — every project a user has ever talked to
keeps its own independent one, just like an automaton instance's own
live state. Scoped through the same session -> ChatSession relationship
as the rest of Tracking: a "Reset conversation" or project deletion
wipes it right along with everything else, exactly like any other
Tracking row for that session. Every read is additionally enriched with
a fixed set of values this class always computes fresh, never
persisted, since they're only ever true "right now" — see
ENV_COMPUTED_KEYS/_compute. Instantiated as ChatService's `env`, same DI
style as tracking/definitions.py's Signals and
metrics/metric_service.py's MetricService.

Two implementations: `Env` itself is a plain in-memory store (both for
its stored/action_set values and, unless told otherwise via
set_replay_instant/set_last_transition_instant, its computed ones stay
"live/production" — see _compute) — a benchmark replay (not introduced
here) can use it directly, updating that internal state once per turn as
it orchestrates its own loop. `PersistedEnv` is production's own
subclass, reading/writing through `db` instead.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from automaton.automaton import Automaton
from automaton.automaton_builder import ENV_COMPUTED_KEYS
from db import Db, _utc_iso

GetUsername = Callable[[], str]
GetActiveProjectName = Callable[[], str]

# Distinguishes "set_replay_instant/set_last_transition_instant was never
# called at all" (production) from "called, and given None" (a replay
# turn with no real elapsed time to report) — the latter is a real,
# meaningful value (see _compute), so None itself can't double as the
# sentinel.
_UNSET = object()

# A computed key's own "no value in this context" marker — never a
# candidate for a real value, so it's always safe to exclude from
# whatever's built out of _compute's result (computed/to_dict/
# merge_if_referenced).
_ABSENT = object()


class Env(object):
    def __init__(
        self,
        db: Db,
        get_username: GetUsername,
        get_active_project_name: GetActiveProjectName,
        stored: dict[str, Any] | None = None,
        action_set: dict[str, Any] | None = None,
    ) -> None:
        self._db = db
        self._get_username = get_username
        self._get_active_project_name = get_active_project_name
        self._stored: dict[str, Any] = dict(stored or {})
        self._action_set: dict[str, Any] = dict(action_set or {})
        # Per-turn replay state — see _compute. Never set in production
        # (see PersistedEnv's own docstring): a benchmark replay (not
        # introduced here) is the only intended caller of the two
        # setters below, once per turn, before this instance is read.
        self._replay_instant: datetime | None | object = _UNSET
        self._last_transition_instant: datetime | None | object = _UNSET

    def set_replay_instant(self, instant: datetime | None) -> None:
        self._replay_instant = instant

    def set_last_transition_instant(self, instant: datetime | None) -> None:
        self._last_transition_instant = instant

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
        though both are merged together (with `computed()`) into what
        actually reaches the turn's own prompt (see to_dict)."""
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
        """Just the persisted, free-form key:values — never the computed
        ones (see computed/ENV_COMPUTED_KEYS). Public — unlike the rest
        of this class's read path, the Inspector's own Env tab needs
        stored and computed values reported separately so it knows which
        are actually editable/deletable (see set_value/delete_key: only
        these are)."""
        return dict(self._stored)

    def computed(self) -> dict[str, Any]:
        """Every ENV_COMPUTED_KEYS value, freshly derived — read-only,
        never a candidate for set_value/delete_key. A key this context
        has no real value for (see _compute) is omitted entirely, never
        included with a fabricated one."""
        result: dict[str, Any] = {}
        for key in ENV_COMPUTED_KEYS:
            value = self._compute(key)
            if value is not _ABSENT:
                result[key] = value
        return result

    def update(self, values: dict[str, Any], message_id: int | None = None) -> None:
        if not values:
            return
        action_set = self.action_set()
        filtered = {key: value for key, value in values.items() if key not in ENV_COMPUTED_KEYS and key not in action_set}
        if not filtered:
            return
        merged = {**self.stored(), **filtered}
        self._write_stored(merged, message_id)

    def set_value(self, key: str, value: str) -> None:
        """The Inspector Env tab's own "click a value to edit it" — a
        thin, explicit alias for update({key: value}), rejecting a
        computed key outright (editing one would just be silently
        overwritten on the very next read anyway, see computed)."""
        if key in ENV_COMPUTED_KEYS:
            raise ValueError(f"'{key}' is a computed value and can't be edited.")
        self.update({key: value})

    def delete_key(self, key: str) -> None:
        """The Inspector Env tab's own "delete this pair" — a no-op if
        `key` isn't currently stored (nothing to remove), never allowed
        for a computed key (there's nothing stored to delete: it's
        derived fresh every read, see computed)."""
        if key in ENV_COMPUTED_KEYS:
            raise ValueError(f"'{key}' is a computed value and can't be deleted.")
        current = self.stored()
        if key not in current:
            return
        del current[key]
        self._write_stored(current)

    def clear(self) -> None:
        """The Inspector Env tab's own "clear all" for the AI section —
        wipes every stored key at once. Computed values (see computed)
        are unaffected, since there's nothing stored about them to begin
        with; action-set ones (see action_set/clear_action_set) live in
        a separate store, untouched by this."""
        self._write_stored({})

    def clear_action_set(self) -> None:
        """The Inspector Env tab's own "clear all" for the ACTION
        section — action_set()'s own equivalent of clear() above. An
        action whose `env:` field still fires afterward will simply
        re-populate whatever it sets again on its next turn, same as any
        other action-set write (see update_action_set)."""
        self._write_action_set({})

    def _now(self) -> datetime | None:
        """The "current instant" every computed key is derived relative
        to — datetime.utcnow() unless a replay turn has explicitly set
        self._replay_instant (possibly to None itself, meaning "no real
        elapsed time available this turn")."""
        if self._replay_instant is _UNSET:
            return datetime.utcnow()
        return self._replay_instant

    def _compute(self, key: str) -> Any:
        """One of ENV_COMPUTED_KEYS — derived fresh from the current
        user+project's own session/transition history, never read off
        `stored()`. `now` stays naive-but-UTC throughout (see db.
        _utc_iso's own docstring): every DateTimeField in this app is
        written the same way, so subtracting two of them here needs no
        timezone reconciliation — only the final ISO-string keys convert
        via _utc_iso, same as everywhere else that reports one outward.

        Production (self._replay_instant/self._last_transition_instant
        never set): identical to before this class stopped threading an
        `until` argument through every call — `now` is always
        datetime.utcnow(), every query unbounded. A replay turn (either
        one explicitly set, even to None) instead scopes every key to
        that turn's own instant(s) — see set_replay_instant/
        set_last_transition_instant — returning _ABSENT for a key with
        no real elapsed time to report rather than fabricating one."""
        if key == "state_duration_in_minutes":
            now = self._now()
            if self._last_transition_instant is _UNSET:
                # Production: identical to before this class stopped
                # threading `until` through every call — no last
                # transition yet is a routine 0.0, never absent.
                project_name = self._get_active_project_name()
                last_transition = self._db.get_last_transition_timestamp(project_name)
                if last_transition is None or now is None:
                    return 0.0
                return round((now - last_transition).total_seconds() / 60, 2)
            # Explicit replay context: no real last-transition instant
            # to report is a genuine "nothing to say" here, not a 0.0.
            if self._last_transition_instant is None or now is None:
                return _ABSENT
            return round((now - self._last_transition_instant).total_seconds() / 60, 2)

        now = self._now()
        if now is None:
            return _ABSENT

        # None when self._replay_instant is still _UNSET (production —
        # unbounded queries, exactly as before) or a real replay
        # instant otherwise (now is None, handled above, whenever
        # self._replay_instant was explicitly set to None).
        replay_bound = None if self._replay_instant is _UNSET else self._replay_instant
        username = self._get_username()
        project_name = self._get_active_project_name()

        if key == "today":
            return now.date().isoformat()
        if key == "time":
            return now.strftime("%H:%M:%S")
        if key == "current_session_duration_in_minutes":
            session = self._db.get_latest_chat_session(username, project_name, until=replay_bound)
            if session is None:
                return 0.0
            return round((now - session["datetime_start"]).total_seconds() / 60, 2)
        if key == "last_user_session_datetime":
            # index 0 is the current/most recent session (as of `now`)
            # — "last" means the one immediately before it (see
            # db.list_chat_sessions' own most-recent-first ordering),
            # None for a user's very first session ever.
            sessions = self._db.list_chat_sessions(username, project_name, until=replay_bound)
            previous = sessions[1] if len(sessions) > 1 else None
            return _utc_iso(previous["datetime_start"]) if previous is not None else None
        if key == "number_of_user_sessions":
            return len(self._db.list_chat_sessions(username, project_name, until=replay_bound))
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in ENV_COMPUTED_KEYS:
            value = self._compute(key)
            return default if value is _ABSENT else value
        # action_set() takes priority on a name collision — an action's
        # own `env:` field is the more deliberate/authoritative source
        # for a key it manages, vs. whatever the model itself happened
        # to report under the same name (see stored/action_set's own
        # docstrings; collisions aren't expected by design, but this
        # keeps precedence well-defined if one ever occurs).
        return {**self.stored(), **self.action_set()}.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        """Every stored value, every action-set one, plus every computed
        one, freshly assembled — what MetadataHandler.build_prompt
        renders back into the turn's own [env]...[/env] block."""
        result = self.stored()
        result.update(self.action_set())
        result.update(self.computed())
        return result

    def merge_if_referenced(self, automaton: Automaton, state_key: str, names: dict[str, Any]) -> dict[str, Any]:
        """`names` (signal/metric values headed for trigger evaluation —
        see automaton.py's Automaton.evaluate_triggers/preview_triggers
        and metrics.metric_service.py's own equivalent), augmented with
        every env value (stored and action-set alike) — but only when at
        least one triggerable action leaving `state_key` actually
        references one (see Automaton.triggers_reference), the same
        skip-when-unused optimization MetricService.merge_if_referenced
        already applies to metrics."""
        stored = self.stored()
        action_set = self.action_set()
        candidate_names = set(ENV_COMPUTED_KEYS) | set(stored.keys()) | set(action_set.keys())
        if not automaton.triggers_reference(state_key, candidate_names):
            return names
        merged = {**stored, **action_set, **self.computed()}
        return {**names, **merged}

    def serialise_as_text(self) -> str:
        return "\n".join(f"{key}: {value}" for key, value in self.to_dict().items())


class PersistedEnv(Env):
    """Production's own Env — reads/writes through `db` instead of the
    base class's in-memory dicts. Never overrides _compute/
    set_replay_instant/set_last_transition_instant: nothing in
    production ever calls the setters, so _compute's own "never set"
    branch (see its docstring) is the only one a PersistedEnv instance
    ever takes — identical to this class's behavior before the
    Env/PersistedEnv split."""

    def __init__(self, db: Db, get_username: GetUsername, get_active_project_name: GetActiveProjectName) -> None:
        super().__init__(db, get_username, get_active_project_name)

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
