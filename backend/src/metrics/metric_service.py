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
    UserAnalyticsDataBuilder,
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
def _user_scoped_metrics() -> list[MetricCalculator]:
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


def _values_dict(pairs: list[tuple[MetricCalculator, MetricResult]]) -> dict[str, float]:
    """Flat {name: value}, omitting any metric whose value is None — a
    trigger referencing an omitted name evaluates False, like any
    signal never estimated."""
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
        self, until: datetime | None = None, project_name: str | None = None
    ) -> list[tuple[MetricCalculator, MetricResult]]:
        """Loads the analytical dataset once per call, then evaluates every
        core metric against it. `until` restricts the dataset; `project_name`
        omitted falls back to the active project."""
        calculator = AnalyticsCalculator(
            self._db, Session().user, project_name or self._project_service.get_active_project_name(), until=until
        )
        return list(zip(calculator.metrics, calculator.calculate_all()))

    def calculate_all(self, until: datetime | None = None, project_name: str | None = None) -> list[dict]:
        """ui_label/ui_description/value per metric. `until`/`project_name`
        omitted means the live active project; both set gives a point-in-time
        view of a specific project, so it never leaks another project's state."""
        return [
            {
                "name": metric.name,
                "ui_label": metric.ui_label,
                "ui_description": metric.ui_description,
                "value": result.value,
            }
            for metric, result in self._calculate(until=until, project_name=project_name)
        ]

    def calculate_values(self) -> dict[str, float]:
        """Flat {name: value} for merging into trigger-evaluation's `names`
        dict and an action's own `env:` eval scope. A metric with no value
        is omitted, like a signal never estimated — never crashes triggers."""
        return _values_dict(self._calculate())

    def for_turn(self) -> UserMetricNamespace:
        """The `metric` namespace for one turn's evaluation scope — cheap to
        call unconditionally: no AnalyticsCalculator is built until an
        expression calls one of the namespace's own methods."""
        return UserMetricNamespace(lambda: AnalyticsCalculator(
            self._db, Session().user, self._project_service.get_active_project_name(),
            metrics=_user_scoped_metrics(), until=datetime.utcnow(),
        ))

    def merge_if_referenced(self, automaton: Automaton, state_key: str, names: dict[str, Any]) -> dict[str, Any]:
        """`names` augmented with core metric values, but only when a
        triggerable action from `state_key` actually references one —
        computing metrics means a full history load, worth skipping otherwise."""
        if not automaton.triggers_reference(state_key, _metric_names()):
            return names
        return {**names, **self.calculate_values()}

    def get_benchmark_metrics(self, session_id: int | None = None, project_name: str | None = None) -> list[dict]:
        """Expert-annotation-vs-actual benchmark metrics for `project_name`
        (omitted: active project) — every annotated session, or just
        `session_id` if given. Adds `sample_count` alongside each score."""
        configuration = BenchmarkConfiguration(
            max_session_duration_in_minutes=self._max_session_duration_in_minutes
        )
        calculator = BenchmarkCalculator(
            self._db, Session().user, project_name or self._project_service.get_active_project_name(),
            configuration=configuration, session_id=session_id,
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


class BenchmarkMetricsProvider:
    """MetricsProvider for a benchmark replay, picking its analytical
    dataset per turn (advance_to): a real session uses full cross-session
    history up to real_timestamp; an imported session (no timestamp)
    scopes to just itself, truncated by message id."""

    def __init__(self, db: Db, username: str, project_name: str, session_id: int) -> None:
        self._db = db
        self._username = username
        self._project_name = project_name
        self._session_id = session_id
        # Set once per turn by advance_to; no default bound so a caller
        # that forgets to call it first fails loudly (AttributeError)
        # rather than silently falling back to "no bound at all".

    def advance_to(self, message_id: int, real_timestamp: datetime | None) -> None:
        self._message_id = message_id
        self._real_timestamp = real_timestamp

    def _calculate(self) -> list[tuple[MetricCalculator, MetricResult]]:
        if self._real_timestamp is not None:
            calculator = AnalyticsCalculator(
                self._db, self._username, self._project_name, until=self._real_timestamp
            )
        else:
            data = UserAnalyticsDataBuilder(self._db, self._username, self._project_name).build_for_session(
                self._session_id, until_message_id=self._message_id
            )
            calculator = AnalyticsCalculator.from_data(data)
        return list(zip(calculator.metrics, calculator.calculate_all()))

    def calculate_values(self) -> dict[str, float]:
        return _values_dict(self._calculate())

    def for_turn(self) -> UserMetricNamespace:
        """The `metric` namespace for one replay turn — same laziness as
        MetricService.for_turn, picking its dataset the same per-turn way
        _calculate above does (full history for a real session, truncated otherwise)."""
        def _build_calculator() -> AnalyticsCalculator:
            if self._real_timestamp is not None:
                return AnalyticsCalculator(
                    self._db, self._username, self._project_name,
                    metrics=_user_scoped_metrics(), until=self._real_timestamp,
                )
            data = UserAnalyticsDataBuilder(self._db, self._username, self._project_name).build_for_session(
                self._session_id, until_message_id=self._message_id
            )
            return AnalyticsCalculator.from_data(data, metrics=_user_scoped_metrics())
        return UserMetricNamespace(_build_calculator)

    def merge_if_referenced(self, automaton: Automaton, state_key: str, names: dict[str, Any]) -> dict[str, Any]:
        if not automaton.triggers_reference(state_key, _metric_names()):
            return names
        return {**names, **self.calculate_values()}
