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
    """Every core metric's reserved name, derived from
    AnalyticsCalculator.default_metrics() — the single registry. Adding
    or removing a metric there keeps every consumer in sync automatically."""
    return frozenset(metric.name for metric in AnalyticsCalculator.default_metrics())


class AnalyticsCalculator(object):
    """Public facade: loads one analytical dataset and evaluates metrics."""

    def __init__(
        self,
        db: AnalyticsDb,
        username: str,
        project_id: str,
        metrics: Iterable[MetricCalculator] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> None:
        """`since`/`until` (naive UTC) restrict the dataset to that window;
        both omitted gives full history. The default metric set is
        filtered to "one_session" scope; an explicit `metrics` is used as-is, unfiltered."""
        self._data = UserAnalyticsDataBuilder(db, username, project_id).build(since=since, until=until)
        self._metrics = self._select_metrics(metrics)

    @classmethod
    def from_data(
        cls, data: UserAnalyticsData, metrics: Iterable[MetricCalculator] | None = None,
    ) -> "AnalyticsCalculator":
        """Builds directly from an already-ready UserAnalyticsData,
        skipping the usual builder step. Same scope filter as the normal
        constructor: `metrics` explicit if given, else "one_session" defaults."""
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
        order — lets a caller pair each MetricResult with its own metric's ui_label/ui_description."""
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
