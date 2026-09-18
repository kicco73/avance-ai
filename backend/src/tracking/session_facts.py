"""The `session` namespace: facts about the current user+project's
session/transition history, each a zero-argument proxy so a value is
only computed if an expression references it. `session.metric.<name>()`
scopes Core Metrics to just this session, unlike the cross-session `metric`."""
from __future__ import annotations

from datetime import datetime

from db import Db, _utc_iso
from metrics.metric_namespace import SessionMetricNamespace
from metrics.metrics_framework import AnalyticsCalculator
from system.web_session import WebSession
from tracking.fixed_project_context import ProjectContext


class _Unset:
    pass


_UNSET = _Unset()


class SessionFacts(object):
    """Production's own live/unbounded shape — nothing here ever calls
    set_replay_instant/set_last_transition_instant. A test replay
    can call those once per turn, scoping every fact to that turn's instant."""

    def __init__(self, db: Db, project_service: ProjectContext) -> None:
        self._db = db
        self._project_service = project_service
        self._replay_instant: datetime | None | _Unset = _UNSET
        self._last_transition_instant: datetime | None | _Unset = _UNSET
        self._metric: SessionMetricNamespace | None = None

    def set_replay_instant(self, instant: datetime | None) -> None:
        self._replay_instant = instant

    def set_last_transition_instant(self, instant: datetime | None) -> None:
        self._last_transition_instant = instant

    def _project_id(self) -> str:
        """Every caller in this class pins a real project — the request
        user's active one, or a FixedProjectContext built off a specific
        session's project_id — never the "nothing active" case the
        Protocol also allows for."""
        project_id = self._project_service.get_active_project_id()
        assert project_id is not None
        return project_id

    def _now(self) -> datetime | None:
        if isinstance(self._replay_instant, _Unset):
            return datetime.utcnow()
        return self._replay_instant

    def state_duration_in_minutes(self) -> float | None:
        now = self._now()
        if isinstance(self._last_transition_instant, _Unset):
            project_id = self._project_id()
            last_transition = self._db.get_last_transition_timestamp(project_id)
            if last_transition is None or now is None:
                return 0.0
            return round((now - last_transition).total_seconds() / 60, 2)
        if self._last_transition_instant is None or now is None:
            return None
        return round((now - self._last_transition_instant).total_seconds() / 60, 2)

    def _replay_bound(self) -> datetime | None:
        return None if isinstance(self._replay_instant, _Unset) else self._replay_instant

    def current_session_duration_in_minutes(self) -> float | None:
        now = self._now()
        if now is None:
            return None
        username = WebSession().user
        project_id = self._project_id()
        session = self._db.get_latest_chat_session(username, project_id, until=self._replay_bound())
        if session is None:
            return 0.0
        return round((now - session["datetime_start"]).total_seconds() / 60, 2)

    def last_user_session_datetime(self) -> str | None:
        now = self._now()
        if now is None:
            return None
        username = WebSession().user
        project_id = self._project_id()
        sessions = self._db.list_chat_sessions(username, project_id, until=self._replay_bound())
        previous = sessions[1] if len(sessions) > 1 else None
        return _utc_iso(previous["datetime_start"]) if previous is not None else None

    def number_of_user_sessions(self) -> int | None:
        now = self._now()
        if now is None:
            return None
        username = WebSession().user
        project_id = self._project_id()
        return len(self._db.list_chat_sessions(username, project_id, until=self._replay_bound()))

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
        username = WebSession().user
        project_id = self._project_id()
        session = self._db.get_latest_chat_session(username, project_id, until=self._replay_bound())
        since = session["datetime_start"] if session is not None else None
        return AnalyticsCalculator(self._db, username, project_id, since=since, until=now)
