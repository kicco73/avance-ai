from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from .dto import MetricResult, UserAnalyticsData
from .interfaces import AnalyticsDb, MetricCalculator
from .metrics import (
    ActivityConsistencyMetric,
    EngagementMetric,
    RetentionMetric,
    SignalStabilityMetric,
    StateStabilityMetric,
)
from .timeline import UserAnalyticsDataBuilder


def metric_names() -> frozenset[str]:
    """Every core metric's reserved name, derived live from
    AnalyticsCalculator.default_metrics() — the one and only registry of
    metrics. Adding or removing a metric there is enough to keep every
    consumer of this (project loading's signal-name collision check,
    trigger-expression validation, auto-tracking's "does this trigger even
    need metrics" check) correct with no further changes."""
    return frozenset(metric.name for metric in AnalyticsCalculator.default_metrics())


class AnalyticsCalculator(object):
    """Public facade: loads one analytical dataset and evaluates metrics."""

    def __init__(
        self,
        db: AnalyticsDb,
        username: str,
        project_name: str,
        metrics: Iterable[MetricCalculator] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> None:
        """`since`/`until` (naive UTC, matching the DB's own timestamp
        convention) restrict the whole analytical dataset to what falls
        within that window — see UserAnalyticsDataBuilder.build. Both
        omitted, this is the full, current history, exactly as before;
        `since` alone (or alongside `until`) is what lets a caller scope
        this to a bounded window, e.g. tracking.session_facts.
        SessionFacts scoping to just the current session's own
        [start, now).

        The *default* metric set is filtered down to whatever's
        meaningful in a "one_session" context (see BaseMetric.scope) —
        every current caller (metrics/metric_service.py's MetricService, for
        both the "Benchmark"/"Edit project" views' own metrics displays
        and trigger evaluation) only ever wants that. An explicitly
        passed `metrics` is used as-is, unfiltered — the caller's own
        explicit choice, not this calculator's to second-guess."""
        self._data = UserAnalyticsDataBuilder(db, username, project_name).build(since=since, until=until)
        self._metrics = self._select_metrics(metrics)

    @classmethod
    def from_data(
        cls, data: UserAnalyticsData, metrics: Iterable[MetricCalculator] | None = None,
    ) -> "AnalyticsCalculator":
        """Skips the usual UserAnalyticsDataBuilder(...).build(...) step
        the normal constructor takes — builds directly from an
        already-ready UserAnalyticsData (e.g.
        UserAnalyticsDataBuilder.build_for_session's own single-session
        one, for a benchmark replay not introduced here). Same scope
        filter as the normal constructor (see _select_metrics):
        `metrics` explicit if given, otherwise only the default metrics
        meaningful in a "one_session" context."""
        instance = cls.__new__(cls)
        instance._data = data
        instance._metrics = cls._select_metrics(metrics)
        return instance

    @staticmethod
    def _select_metrics(metrics: Iterable[MetricCalculator] | None) -> tuple[MetricCalculator, ...]:
        if metrics is not None:
            return tuple(metrics)
        return tuple(m for m in AnalyticsCalculator.default_metrics() if "one_session" in m.scope)

    @property
    def metrics(self) -> tuple[MetricCalculator, ...]:
        """The metric instances calculate_all() evaluates, in the same
        order — lets a caller pair each MetricResult with its own metric's
        ui_label/ui_description without instantiating a second set."""
        return self._metrics

    @staticmethod
    def default_metrics() -> tuple[MetricCalculator, ...]:
        return (
            EngagementMetric(),
            RetentionMetric(),
            ActivityConsistencyMetric(),
            StateStabilityMetric(),
            SignalStabilityMetric(),
        )

    def calculate_all(self) -> list[MetricResult]:
        return [metric.calculate(self._data) for metric in self._metrics]

    def calculate(self, metric: MetricCalculator) -> MetricResult:
        return metric.calculate(self._data)
