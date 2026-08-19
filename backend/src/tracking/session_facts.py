"""The `session` namespace a trigger/`env:` expression resolves against
(see tracking.evaluation_scope.EvaluationScopeBuilder) — facts about the
current user+project's own session/transition history: session
duration, the previous session's own timestamp, how many sessions
total, and how long the conversation has sat in its current state.
Every method is a zero-argument proxy (called as `session.
number_of_user_sessions()`, never read as a bare attribute) so a value
is only ever computed — a real db query — if an expression actually
references it. Moved verbatim out of tracking.env.Env, which used to
compute these itself (see ENV_COMPUTED_KEYS, now gone) alongside its own
unrelated stored/action_set responsibilities.

`metric` (see the `metric` property below) is this class's one
sub-namespace: `session.metric.<name>()` — the Analytics Core Metrics
meaningful over just the current session's own window, as opposed to the
`metric` namespace's own (see metrics.metric_service.MetricService.
for_turn), which spans the user's whole cross-session history instead.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from db import Db, _utc_iso
from metrics.metric_namespace import SessionMetricNamespace
from metrics.metrics_framework import AnalyticsCalculator

GetUsername = Callable[[], str]
GetActiveProjectName = Callable[[], str]

# Distinguishes "set_replay_instant/set_last_transition_instant was never
# called at all" (production) from "called, and given None" (a replay
# turn with no real elapsed time to report) — the latter is a real,
# meaningful value, so None itself can't double as the sentinel.
_UNSET = object()


class SessionFacts(object):
    """Two implementations mirroring tracking.env.Env's own split: this
    class itself is production's own live/unbounded shape (nothing ever
    calls set_replay_instant/set_last_transition_instant) — a benchmark
    replay (not introduced here) can call those once per turn as it
    orchestrates its own loop, scoping every fact below to that turn's
    own instant(s) instead."""

    def __init__(self, db: Db, get_username: GetUsername, get_active_project_name: GetActiveProjectName) -> None:
        self._db = db
        self._get_username = get_username
        self._get_active_project_name = get_active_project_name
        self._replay_instant: datetime | None | object = _UNSET
        self._last_transition_instant: datetime | None | object = _UNSET
        self._metric: SessionMetricNamespace | None = None

    def set_replay_instant(self, instant: datetime | None) -> None:
        self._replay_instant = instant

    def set_last_transition_instant(self, instant: datetime | None) -> None:
        self._last_transition_instant = instant

    def _now(self) -> datetime | None:
        if self._replay_instant is _UNSET:
            return datetime.utcnow()
        return self._replay_instant

    def state_duration_in_minutes(self) -> float | None:
        now = self._now()
        if self._last_transition_instant is _UNSET:
            # Production: no last transition yet is a routine 0.0, never
            # a "nothing to report" None.
            project_name = self._get_active_project_name()
            last_transition = self._db.get_last_transition_timestamp(project_name)
            if last_transition is None or now is None:
                return 0.0
            return round((now - last_transition).total_seconds() / 60, 2)
        # Explicit replay context: no real last-transition instant to
        # report is a genuine "nothing to say" here, not a 0.0.
        if self._last_transition_instant is None or now is None:
            return None
        return round((now - self._last_transition_instant).total_seconds() / 60, 2)

    def _replay_bound(self) -> datetime | None:
        # None when self._replay_instant is still _UNSET (production —
        # unbounded queries) or a real replay instant otherwise.
        return None if self._replay_instant is _UNSET else self._replay_instant

    def current_session_duration_in_minutes(self) -> float | None:
        now = self._now()
        if now is None:
            return None
        username = self._get_username()
        project_name = self._get_active_project_name()
        session = self._db.get_latest_chat_session(username, project_name, until=self._replay_bound())
        if session is None:
            return 0.0
        return round((now - session["datetime_start"]).total_seconds() / 60, 2)

    def last_user_session_datetime(self) -> str | None:
        now = self._now()
        if now is None:
            return None
        username = self._get_username()
        project_name = self._get_active_project_name()
        # index 0 is the current/most recent session (as of `now`) —
        # "last" means the one immediately before it (see db.
        # list_chat_sessions' own most-recent-first ordering), None for
        # a user's very first session ever.
        sessions = self._db.list_chat_sessions(username, project_name, until=self._replay_bound())
        previous = sessions[1] if len(sessions) > 1 else None
        return _utc_iso(previous["datetime_start"]) if previous is not None else None

    def number_of_user_sessions(self) -> int | None:
        now = self._now()
        if now is None:
            return None
        username = self._get_username()
        project_name = self._get_active_project_name()
        return len(self._db.list_chat_sessions(username, project_name, until=self._replay_bound()))

    @property
    def metric(self) -> SessionMetricNamespace:
        """`session.metric` — see metrics.metric_namespace.
        SessionMetricNamespace's own docstring for the laziness this
        relies on: accessing this property itself does no DB work at
        all, not even the current-session lookup below, which only runs
        the first time an expression actually calls one of the returned
        namespace's own methods."""
        if self._metric is None:
            self._metric = SessionMetricNamespace(self._build_session_metric_calculator)
        return self._metric

    def _build_session_metric_calculator(self) -> AnalyticsCalculator:
        now = self._now()
        username = self._get_username()
        project_name = self._get_active_project_name()
        session = self._db.get_latest_chat_session(username, project_name, until=self._replay_bound())
        since = session["datetime_start"] if session is not None else None
        return AnalyticsCalculator(self._db, username, project_name, since=since, until=now)
