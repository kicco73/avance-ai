from __future__ import annotations

from typing import Literal

# The dataset a metric was designed to be meaningful over. Every current
# consumer filters down to "one_session" unconditionally; the other two
# values still document what a metric like RetentionMetric requires.
MetricScope = Literal["one_session", "all_sessions_per_user", "all_sessions"]

ALL_METRIC_SCOPES: frozenset[MetricScope] = frozenset(
    {"one_session", "all_sessions_per_user", "all_sessions"}
)
