from __future__ import annotations

from ..scope import ALL_METRIC_SCOPES, MetricScope
from .dto import BenchmarkMetricResult, BenchmarkObservation


class BenchmarkMetric(object):
    # Every context by default — a subclass narrows this when the metric
    # only makes sense over a specific dataset (e.g. one needing the full
    # cross-session benchmark to say anything about bias/dispersion).
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

    def calculate(self, observations: tuple[BenchmarkObservation, ...]) -> BenchmarkMetricResult:
        raise NotImplementedError
