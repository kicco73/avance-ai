from __future__ import annotations

from typing import Literal

# The dataset a metric was designed to be meaningful over. Every current
# consumer (the "Label sessions"/"Edit project" views, both inherently
# about one session at a time, and trigger evaluation, always run within
# one active session) only ever wants "one_session" — see
# AnalyticsCalculator/BenchmarkCalculator, which filter their own metrics
# down to that unconditionally. The other two values still document what
# a metric like RetentionMetric or BenchmarkStabilityMetric actually
# requires to mean anything, even with no consumer asking for them yet.
MetricScope = Literal["one_session", "all_sessions_per_user", "all_sessions"]

ALL_METRIC_SCOPES: frozenset[MetricScope] = frozenset(
    {"one_session", "all_sessions_per_user", "all_sessions"}
)
