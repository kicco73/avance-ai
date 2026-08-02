"""Per-(user, project) "environment" memory — free-form key:value facts
the model can extend at will via [env]...[/env] (see chat.
metadata_handler.MetadataHandler), persisted as a dedicated env-only row
on the db.py Signals event log (see db.Db.get_env/set_env, and Signals'
own docstring) — every project a user has ever talked to keeps its own
independent one, just like an automaton instance's own live state.
Scoped through the same session -> ChatSession relationship as the rest
of Signals: a "Reset conversation" or project deletion wipes it right
along with everything else, exactly like any other Signals row for that
session. Every read is additionally enriched with a fixed set of values
this class always computes fresh, never persisted, since they're only
ever true "right now" — see ENV_COMPUTED_KEYS/_compute. Instantiated as
ChatService's `env`, same DI style as chat/signals.py's Signals and
chat/metrics_service.py's ChatMetrics.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from automaton.automaton import Automaton
from automaton.automaton_builder import ENV_COMPUTED_KEYS
from db import Db, _utc_iso

GetUsername = Callable[[], str]
GetActiveProjectName = Callable[[], str]


class Env(object):
    def __init__(self, db: Db, get_username: GetUsername, get_active_project_name: GetActiveProjectName) -> None:
        self._db = db
        self._get_username = get_username
        self._get_active_project_name = get_active_project_name

    def action_set(self, until: datetime | None = None) -> dict[str, Any]:
        """Just the persisted values an action's own YAML `env:` field set
        (see automaton_builder.py's _build_action/Automaton.
        eval_action_env, and update_action_set below) — kept in a
        separate store from `stored()`'s model-reported ones so the
        Inspector Env tab can badge the two apart ("SET" vs "AI"), even
        though both are merged together (with `computed()`) into what
        actually reaches the turn's own prompt (see to_dict). Same
        `until` convention as stored()."""
        return self._db.get_action_env(self._get_active_project_name(), self._get_username(), until=until)

    def update_action_set(self, values: dict[str, Any]) -> None:
        """action_set()'s own update — an action firing (see chat_service.
        py's/auto_tracker.py's own _apply_action_env), never the model
        itself (that's update(), for `[env]`-reported values). Merges
        onto whatever's already action-set, same no-op-for-falsy rule as
        update()."""
        if not values:
            return
        merged = {**self.action_set(), **values}
        self._db.set_action_env(self._get_active_project_name(), merged, self._get_username())

    def stored(self, until: datetime | None = None) -> dict[str, Any]:
        """Just the persisted, free-form key:values — never the computed
        ones (see computed/ENV_COMPUTED_KEYS). `until` (naive-but-UTC):
        as they stood at or before that point, for the "Label sessions"
        view's point-in-time Inspector (see ChatService.get_env);
        omitted (None) means live/current. Public — unlike the rest of
        this class's read path, the Inspector's own Env tab needs stored
        and computed values reported separately so it knows which are
        actually editable/deletable (see set_value/delete_key: only
        these are)."""
        return self._db.get_env(self._get_active_project_name(), self._get_username(), until=until)

    def computed(self, until: datetime | None = None) -> dict[str, Any]:
        """Every ENV_COMPUTED_KEYS value, freshly derived — read-only,
        never a candidate for set_value/delete_key."""
        return {key: self._compute(key, until) for key in ENV_COMPUTED_KEYS}

    def update(self, values: dict[str, Any]) -> None:
        """Merges `values` (freshly parsed from an incoming [env] tag —
        see MetadataHandler._parse_env_tag) onto whatever's already
        persisted for the current user+project, overwriting matching
        keys — a no-op for an empty/falsy `values` (most turns don't
        report any, and there's nothing to persist or query for). Always
        live: there's no "editing history" here, only ever the current
        value going forward.

        Silently drops any key that isn't genuinely the model's own to
        report: a computed one (ENV_COMPUTED_KEYS), or one currently
        action-set (see action_set/update_action_set) — both are shown to
        the model as part of the very same [env] block it's asked to
        mirror back (see MetadataHandler.build_prompt's env.to_dict()),
        and despite being told to "only include a key when reporting
        something new", a model will often echo the whole block back
        verbatim regardless. Without this filter that echo would
        re-land here and show up duplicated under the Inspector's "AI"
        section even though it's really computed/action-set — see the
        bug this was written to fix, reported against
        WRONG_ANSWERS_ON_CURRENT_STEP (an action-set key) showing up
        under "AI" too."""
        if not values:
            return
        action_set = self.action_set()
        filtered = {key: value for key, value in values.items() if key not in ENV_COMPUTED_KEYS and key not in action_set}
        if not filtered:
            return
        merged = {**self.stored(), **filtered}
        self._db.set_env(self._get_active_project_name(), merged, self._get_username())

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
        self._db.set_env(self._get_active_project_name(), current, self._get_username())

    def clear(self) -> None:
        """The Inspector Env tab's own "clear all" for the AI section —
        wipes every stored key at once. Computed values (see computed)
        are unaffected, since there's nothing stored about them to begin
        with; action-set ones (see action_set/clear_action_set) live in
        a separate store, untouched by this."""
        self._db.set_env(self._get_active_project_name(), {}, self._get_username())

    def clear_action_set(self) -> None:
        """The Inspector Env tab's own "clear all" for the ACTION
        section — action_set()'s own equivalent of clear() above. An
        action whose `env:` field still fires afterward will simply
        re-populate whatever it sets again on its next turn, same as any
        other action-set write (see update_action_set)."""
        self._db.set_action_env(self._get_active_project_name(), {}, self._get_username())

    def _compute(self, key: str, until: datetime | None = None) -> Any:
        """One of ENV_COMPUTED_KEYS — derived fresh from the current
        user+project's own session/transition history, never read off
        `stored()`. `now` stays naive-but-UTC throughout (see db.
        _utc_iso's own docstring): every DateTimeField in this app is
        written the same way, so subtracting two of them here needs no
        timezone reconciliation — only the final ISO-string keys convert
        via _utc_iso, same as everywhere else that reports one outward.
        `until` (also naive-but-UTC): stands in for "now" itself, so a
        point-in-time read reconstructs what this value actually was as
        of that moment (e.g. "how long had we been in this state, as of
        this message") rather than what it is this instant."""
        now = until if until is not None else datetime.utcnow()
        username = self._get_username()
        project_name = self._get_active_project_name()

        if key == "today":
            return now.date().isoformat()
        if key == "time":
            return now.strftime("%H:%M:%S")
        if key == "current_session_duration_in_minutes":
            session = self._db.get_latest_chat_session(username, project_name, until=until)
            if session is None:
                return 0.0
            return round((now - session["datetime_start"]).total_seconds() / 60, 2)
        if key == "last_user_session_datetime":
            # index 0 is the current/most recent session (as of `until`,
            # if given) — "last" means the one immediately before it (see
            # db.list_chat_sessions' own most-recent-first ordering),
            # None for a user's very first session ever.
            sessions = self._db.list_chat_sessions(username, project_name, until=until)
            previous = sessions[1] if len(sessions) > 1 else None
            return _utc_iso(previous["datetime_start"]) if previous is not None else None
        if key == "number_of_user_sessions":
            return len(self._db.list_chat_sessions(username, project_name, until=until))
        if key == "state_duration_in_minutes":
            # real_only=True (see get_last_transition_timestamp): a
            # self-loop never counts as "arriving" at the current state
            # again, so it doesn't reset this clock either.
            last_transition = self._db.get_last_transition_timestamp(project_name, until=until)
            if last_transition is None:
                return 0.0
            return round((now - last_transition).total_seconds() / 60, 2)
        raise KeyError(key)

    def get(self, key: str, default: Any = None, until: datetime | None = None) -> Any:
        if key in ENV_COMPUTED_KEYS:
            return self._compute(key, until)
        # action_set() takes priority on a name collision — an action's
        # own `env:` field is the more deliberate/authoritative source
        # for a key it manages, vs. whatever the model itself happened
        # to report under the same name (see stored/action_set's own
        # docstrings; collisions aren't expected by design, but this
        # keeps precedence well-defined if one ever occurs).
        return {**self.stored(until), **self.action_set(until)}.get(key, default)

    def to_dict(self, until: datetime | None = None) -> dict[str, Any]:
        """Every stored value, every action-set one, plus every computed
        one, freshly assembled — what MetadataHandler.build_prompt
        renders back into the turn's own [env]...[/env] block. Always
        live (`until` omitted) there: only ChatService.get_env's own
        point-in-time Inspector reads ever pass it."""
        result = self.stored(until)
        result.update(self.action_set(until))
        result.update(self.computed(until))
        return result

    def merge_if_referenced(self, automaton: Automaton, state_key: str, names: dict[str, Any]) -> dict[str, Any]:
        """`names` (signal/metric values headed for trigger evaluation —
        see automaton.py's Automaton.evaluate_triggers/preview_triggers
        and chat/metrics_service.py's own equivalent), augmented with
        every env value (stored and action-set alike) — but only when at
        least one triggerable action leaving `state_key` actually
        references one (see Automaton.triggers_reference), the same
        skip-when-unused optimization ChatMetrics.merge_if_referenced
        already applies to metrics."""
        stored = self.stored()
        action_set = self.action_set()
        candidate_names = set(ENV_COMPUTED_KEYS) | set(stored.keys()) | set(action_set.keys())
        if not automaton.triggers_reference(state_key, candidate_names):
            return names
        merged = {**stored, **action_set}
        for key in ENV_COMPUTED_KEYS:
            merged[key] = self._compute(key)
        return {**names, **merged}
