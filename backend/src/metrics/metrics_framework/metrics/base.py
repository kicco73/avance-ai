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

    @staticmethod
    def unavailable(name: str, reason: str | None = None) -> MetricResult:
        """A metric that has no meaningful value to report in the
        current context (e.g. no real elapsed time to normalize
        against) — never a fabricated number. `reason`, if given, is
        recorded under `metadata` for whoever's debugging why."""
        return MetricResult(
            name=name,
            value=None,
            metadata={"reason": reason} if reason else {},
            calculated_at=datetime.now(timezone.utc),
        )
