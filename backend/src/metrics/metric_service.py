"""MetricService covers a project's metrics: metrics_framework's core
always-on metrics and its benchmark metrics. A leaf service — depends
only on `db`, `project_service`, and metrics_framework, never on
ChatService/TrackingService."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from automaton.automaton import Automaton
from chat.session_manager import DEFAULT_OPEN_WINDOW_MINUTES
from db import Db
from metrics.metric_namespace import UserMetricNamespace
from metrics.metrics_framework import (
    AnalyticsCalculator,
    BenchmarkCalculator,
    BenchmarkConfiguration,
    MetricCalculator,
    MetricResult,
)
from metrics.metrics_framework import metric_names as _metric_names
from session import Session

if TYPE_CHECKING:
    # Deferred: project.project_service -> tracking.tracking_engine ->
    # tracking.evaluation_scope -> MetricService from this very module —
    # a real top-level import here would be circular. Safe as a type-only
    # import since `from __future__ import annotations` (above) never
    # evaluates it at runtime.
    from project.project_service import ProjectService

# Metrics scoped to "all_sessions_per_user" but not "one_session" — the
# `metric` namespace's own membership. Excluding "one_session" matters:
# without it, session-scoped metrics like Engagement would match too.
# Public (no leading underscore): also used by testing.metrics_provider.TestMetricsProvider.
def user_scoped_metrics() -> list[MetricCalculator]:
    return [
        metric for metric in AnalyticsCalculator.default_metrics()
        if "all_sessions_per_user" in metric.scope and "one_session" not in metric.scope
    ]

class MetricsProvider(Protocol):
    """Whatever a caller needs from a metrics source to evaluate
    triggers/action env — satisfied structurally (duck-typed), no
    explicit inheritance needed."""

    def calculate_values(self) -> dict[str, float]:
        ...

    def merge_if_referenced(self, automaton: Automaton, state_key: str, names: dict[str, Any]) -> dict[str, Any]:
        ...


def values_dict(pairs: list[tuple[MetricCalculator, MetricResult]]) -> dict[str, float]:
    """Flat {name: value}, omitting any metric whose value is None — a
    trigger referencing an omitted name evaluates False, like any
    signal never estimated. Public: also used by
    testing.metrics_provider.TestMetricsProvider."""
    return {metric.name: result.value for metric, result in pairs if result.value is not None}


class MetricService(object):
    def __init__(
        self,
        db: Db,
        project_service: "ProjectService",
        # Same source of truth as ChatSessionManager's own open-session
        # window default — reused rather than duplicated, and never taken
        # from a live ChatSessionManager instance (no other reason to depend on chat/).
        # A static value, unlike project_service/Session — known once at
        # boot from config.py, never needs to be "read fresh".
        max_session_duration_in_minutes: float = DEFAULT_OPEN_WINDOW_MINUTES,
    ) -> None:
        self._db = db
        self._project_service = project_service
        self._max_session_duration_in_minutes = max_session_duration_in_minutes

    @property
    def max_session_duration_in_minutes(self) -> float:
        return self._max_session_duration_in_minutes

    def _calculate(
        self, until: datetime | None = None, project_id: str | None = None,
        metrics: list[MetricCalculator] | None = None, username: str | None = None,
    ) -> list[tuple[MetricCalculator, MetricResult]]:
        """Loads the analytical dataset once per call, then evaluates every
        core metric against it. `until` restricts the dataset; `project_id`
        omitted falls back to the active project; `username` omitted falls
        back to the caller's own session. `metrics` omitted uses
        AnalyticsCalculator's own default (scoped to "one_session")."""
        calculator = AnalyticsCalculator(
            self._db, username or Session().user, project_id or self._project_service.get_active_project_id(),
            metrics=metrics, until=until,
        )
        return list(zip(calculator.metrics, calculator.calculate_all()))

    def calculate_all(
        self, until: datetime | None = None, project_id: str | None = None,
        include_all_scopes: bool = False, username: str | None = None,
    ) -> list[dict]:
        """ui_label/ui_description/value per metric. `until`/`project_id`
        omitted means the live active project; both set gives a point-in-time
        view of a specific project, so it never leaks another project's state.
        `include_all_scopes`: every core metric (e.g. Retention/Activity
        Consistency, which need more than one session) instead of the usual
        "one_session" subset — for a view aggregating a whole project's
        history rather than a single live session. `username` omitted means
        the caller's own sessions — Manage Users' statistics panel passes
        the Explorer's selected user instead, to inspect *their* sessions."""
        metrics = AnalyticsCalculator.default_metrics() if include_all_scopes else None
        return [
            {
                "name": metric.name,
                "ui_label": metric.ui_label,
                "ui_description": metric.ui_description,
                "value": result.value,
            }
            for metric, result in self._calculate(until=until, project_id=project_id, metrics=metrics, username=username)
        ]

    def calculate_values(self) -> dict[str, float]:
        """Flat {name: value} for merging into trigger-evaluation's `names`
        dict and an action's own `env:` eval scope. A metric with no value
        is omitted, like a signal never estimated — never crashes triggers."""
        return values_dict(self._calculate())

    def for_turn(self) -> UserMetricNamespace:
        """The `metric` namespace for one turn's evaluation scope — cheap to
        call unconditionally: no AnalyticsCalculator is built until an
        expression calls one of the namespace's own methods."""
        return UserMetricNamespace(lambda: AnalyticsCalculator(
            self._db, Session().user, self._project_service.get_active_project_id(),
            metrics=user_scoped_metrics(), until=datetime.utcnow(),
        ))

    def merge_if_referenced(self, automaton: Automaton, state_key: str, names: dict[str, Any]) -> dict[str, Any]:
        """`names` augmented with core metric values, but only when a
        triggerable action from `state_key` actually references one —
        computing metrics means a full history load, worth skipping otherwise."""
        if not automaton.triggers_reference(state_key, _metric_names()):
            return names
        return {**names, **self.calculate_values()}

    def get_benchmark_metrics(self, session_id: int | None = None, project_id: str | None = None) -> list[dict]:
        """Expert-annotation-vs-actual benchmark metrics for `project_id`
        (omitted: active project) — every annotated session, or just
        `session_id` if given. Adds `sample_count` alongside each score."""
        configuration = BenchmarkConfiguration(
            max_session_duration_in_minutes=self._max_session_duration_in_minutes
        )
        resolved_project_id = project_id or self._project_service.get_active_project_id()
        # This is the frontend's own metric *catalog* (name -> ui_label/
        # ui_description) — every metric belongs in it regardless of
        # session_id, unlike a real run's own results, which stay scoped
        # to whatever's meaningful for that run (see BenchmarkCalculator's
        # own default "one_session" filtering).
        unfiltered_metrics = BenchmarkCalculator(
            self._db, Session().user, resolved_project_id, configuration=configuration, session_id=session_id,
        ).default_metrics()
        calculator = BenchmarkCalculator(
            self._db, Session().user, resolved_project_id,
            configuration=configuration, session_id=session_id, metrics=unfiltered_metrics,
        )
        results = calculator.calculate_all()
        return [
            {
                "name": metric.name,
                "ui_label": metric.ui_label,
                "ui_description": metric.ui_description,
                "value": result.value,
                "sample_count": result.sample_count,
            }
            for metric, result in zip(calculator.metrics, results)
        ]
