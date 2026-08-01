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
    ) -> None:
        self._data = UserAnalyticsDataBuilder(db, username, project_name).build()
        self._metrics = tuple(metrics) if metrics is not None else self.default_metrics()

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
