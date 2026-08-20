"""Shared laziness for the two metric namespaces — `session.metric` and
`metric` — each backed by its own AnalyticsCalculator, built and cached
only on first use, scoped to one turn's lifetime."""
from __future__ import annotations

from typing import Callable

from .metrics_framework import AnalyticsCalculator, MetricCalculator


class LazyMetricNamespace(object):
    def __init__(self, build_calculator: Callable[[], AnalyticsCalculator]) -> None:
        self._build_calculator = build_calculator
        self._calculator: AnalyticsCalculator | None = None
        self._by_name: dict[str, MetricCalculator] | None = None

    def _value(self, name: str) -> float | None:
        if self._calculator is None:
            self._calculator = self._build_calculator()
            self._by_name = {metric.name: metric for metric in self._calculator.metrics}
        metric = self._by_name.get(name)
        if metric is None:
            return None
        return self._calculator.calculate(metric).value


class SessionMetricNamespace(LazyMetricNamespace):
    """`session.metric` — the Analytics Core Metrics meaningful over just
    the current session's own window (scope "one_session" — Engagement,
    State Stability, Signal Stability)."""

    def engagement(self) -> float | None:
        return self._value("engagement")

    def state_stability(self) -> float | None:
        return self._value("state_stability")

    def signal_stability(self) -> float | None:
        return self._value("signal_stability")


class UserMetricNamespace(LazyMetricNamespace):
    """`metric` — the Analytics Core Metrics meaningful only over the
    user's whole cross-session history (scope "all_sessions_per_user" —
    Retention, Activity Consistency)."""

    def retention(self) -> float | None:
        return self._value("retention")

    def activity_consistency(self) -> float | None:
        return self._value("activity_consistency")
