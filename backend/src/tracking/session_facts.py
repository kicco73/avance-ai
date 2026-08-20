"""The `session` namespace: facts about the current user+project's
session/transition history, each a zero-argument proxy so a value is
only computed if an expression references it. `session.metric.<name>()`
scopes Core Metrics to just this session, unlike the cross-session `metric`."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from db import Db, _utc_iso
from metrics.metric_namespace import SessionMetricNamespace
from metrics.metrics_framework import AnalyticsCalculator

GetUsername = Callable[[], str]
GetActiveProjectName = Callable[[], str]

# Distinguishes "never called" (production) from "called, and given
# None" (replay, no real elapsed time) — None is itself a meaningful
# value here, so it can't double as the sentinel.
_UNSET = object()


class SessionFacts(object):
    """Production's own live/unbounded shape — nothing here ever calls
    set_replay_instant/set_last_transition_instant. A benchmark replay
    can call those once per turn, scoping every fact to that turn's instant."""

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
        # "last" means the one immediately before it, most-recent-first
        # ordering. None for a user's very first session ever.
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
        """Accessing this property does no DB work at all — not even the
        current-session lookup below, which only runs the first time an
        expression actually calls one of the returned namespace's methods."""
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
