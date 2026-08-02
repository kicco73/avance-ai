from __future__ import annotations

from datetime import datetime, timezone

from ..dto import MetricResult, UserAnalyticsData
from ..scope import ALL_METRIC_SCOPES, MetricScope


class BaseMetric(object):
    # Every context by default — a subclass narrows this when the metric
    # only makes sense over a specific dataset (see RetentionMetric,
    # ActivityConsistencyMetric).
    scope: frozenset[MetricScope] = ALL_METRIC_SCOPES

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def ui_label(self) -> str:
        raise NotImplementedError

    @property
    def ui_description(self) -> str:
        raise NotImplementedError

    def calculate(self, data: UserAnalyticsData) -> MetricResult:
        raise NotImplementedError

    @staticmethod
    def result(name: str, value: float, components: dict[str, float] | None = None) -> MetricResult:
        return MetricResult(
            name=name,
            value=max(0.0, min(100.0, float(value))),
            components=components or {},
            calculated_at=datetime.now(timezone.utc),
        )
