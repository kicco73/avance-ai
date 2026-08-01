from __future__ import annotations

from typing import Protocol

from .dto import BenchmarkMetricResult, BenchmarkObservation


class BenchmarkMetric(object):
    @property
    def name(self) -> str:
        raise NotImplementedError

    def calculate(self, observations: tuple[BenchmarkObservation, ...]) -> BenchmarkMetricResult:
        raise NotImplementedError
