from __future__ import annotations

from typing import Literal
MetricScope = Literal["one_session", "all_sessions_per_user", "all_sessions"]

ALL_METRIC_SCOPES: frozenset[MetricScope] = frozenset(
    {"one_session", "all_sessions_per_user", "all_sessions"}
)
