from __future__ import annotations

import pandas as pd

from ..dto import MetricResult, UserAnalyticsData
from ..normalization import Normalizer
from .base import BaseMetric


class RetentionMetric(BaseMetric):
    """Measures return behavior rather than raw activity volume.

    Uses the fraction of observed gaps that end with another session inside
    the configured retention horizon. With fewer than two sessions the score
    is zero because retention cannot be observed yet.
    """

    # Return behavior is meaningless within a single session — needs at
    # least the user's own session history to observe a gap at all.
    scope = frozenset({"all_sessions_per_user", "all_sessions"})

    def __init__(self, horizon_days: float = 14.0) -> None:
        self._horizon_days = horizon_days

    @property
    def name(self) -> str:
        return "retention"

    @property
    def ui_label(self) -> str:
        return "Retention"

    @property
    def ui_description(self) -> str:
        return "How consistently the user returns over time, rather than raw activity volume."

    def calculate(self, data: UserAnalyticsData) -> MetricResult:
        if len(data.sessions) < 2:
            return self.result(self.name, 0.0, {"return_rate": 0.0})

        starts = pd.to_datetime(data.sessions["datetime_start"], utc=True).sort_values()
        gaps = starts.diff().dropna().dt.total_seconds() / 86400.0
        if gaps.empty:
            return self.result(self.name, 0.0, {"return_rate": 0.0})

        retained = float((gaps <= self._horizon_days).sum())
        return_rate = retained / float(len(gaps)) * 100.0
        score = Normalizer.clamp(return_rate)
        return self.result(self.name, score, {"return_rate": score})
