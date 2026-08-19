"""Shared laziness for the two metric-bearing evaluation-scope
namespaces — `session.metric` (tracking.session_facts.SessionFacts.metric)
and `metric` (MetricService.for_turn) — each backed by its own
AnalyticsCalculator, but built off a different window (the current
session's own [start, now) vs. the user's whole cross-session history).

Every namespace attribute is a zero-argument proxy, same convention as
SystemFacts/SessionFacts: the AnalyticsCalculator itself (an eager,
whole-history-or-window DB load — see AnalyticsCalculator.__init__) is
never constructed until an expression actually calls one of these
methods, so an unreferenced `session.metric`/`metric` costs nothing, same
as an unreferenced system/session fact. Once built, it's cached for this
object's own lifetime (one turn — see EvaluationScopeBuilder.build,
which constructs a fresh namespace instance every turn) so a trigger
referencing more than one metric off the same namespace still costs
exactly one dataset load, not one per metric (see AnalyticsCalculator.
calculate, which evaluates a single metric off an already-loaded
dataset).
"""
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
