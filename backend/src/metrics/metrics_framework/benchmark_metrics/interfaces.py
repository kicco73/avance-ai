from __future__ import annotations

from ..scope import ALL_METRIC_SCOPES, MetricScope
from .dto import BenchmarkMetricResult, BenchmarkObservation


class BenchmarkMetric(object):
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
