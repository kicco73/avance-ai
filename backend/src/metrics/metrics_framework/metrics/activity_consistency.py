from __future__ import annotations

import pandas as pd

from ..dto import MetricResult, UserAnalyticsData
from .base import BaseMetric


class ActivityConsistencyMetric(BaseMetric):
    """Regularity of user activity across calendar days: lower variation
    in per-day message counts scores higher. Needs at least two active
    days, otherwise the score is zero for lack of evidence."""

    # Regularity across calendar days is meaningless within a single
    # session — needs the user's own broader activity history.
    scope = frozenset({"all_sessions_per_user", "all_sessions"})

    @property
    def name(self) -> str:
        return "activity_consistency"

    @property
    def ui_label(self) -> str:
        return "Activity consistency"

    @property
    def ui_description(self) -> str:
        return "How evenly the user's activity is distributed over time, rather than concentrated in bursts."

    def calculate(self, data: UserAnalyticsData) -> MetricResult:
        messages = data.user_messages
        if messages.empty:
            return self.result(self.name, 0.0)

        timestamps = pd.to_datetime(messages["timestamp"], utc=True)
        counts = timestamps.dt.floor("D").value_counts().sort_index()
        if len(counts) < 2:
            return self.result(self.name, 0.0, {"coefficient_of_variation": 100.0})

        mean = float(counts.mean())
        std = float(counts.std(ddof=0))
        if mean == 0.0:
            return self.result(self.name, 0.0)

        cv = std / mean
        score = 100.0 / (1.0 + cv)
        return self.result(self.name, score, {
            "coefficient_of_variation": min(100.0, cv * 100.0),
        })
