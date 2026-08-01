from __future__ import annotations

from collections.abc import Iterable

from .dto import MetricResult
from .interfaces import AnalyticsDb, MetricCalculator
from .metrics import (
    ActivityConsistencyMetric,
    EngagementMetric,
    RetentionMetric,
    SignalStabilityMetric,
    StateStabilityMetric,
)
from .timeline import UserAnalyticsDataBuilder


class AnalyticsCalculator(object):
    """Public facade: loads one analytical dataset and evaluates metrics."""

    def __init__(
        self,
        db: AnalyticsDb,
        username: str,
        project_name: str,
        metrics: Iterable[MetricCalculator] | None = None,
    ) -> None:
        self._data = UserAnalyticsDataBuilder(db, username, project_name).build()
        self._metrics = tuple(metrics) if metrics is not None else self.default_metrics()

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
