from __future__ import annotations

import pandas as pd

from ..dto import MetricResult, UserAnalyticsData
from ..normalization import Normalizer
from .base import BaseMetric


class StateStabilityMetric(BaseMetric):
    """How rarely the observed state changes over elapsed time. Domain-
    agnostic: state names carry no ordering, only real transitions
    (`old_state != new_state`) count."""

    def __init__(self, reference_transitions_per_30_days: float = 4.0) -> None:
        self._reference_transitions_per_30_days = reference_transitions_per_30_days

    @property
    def name(self) -> str:
        return "state_stability"

    @property
    def ui_label(self) -> str:
        return "State stability"

    @property
    def ui_description(self) -> str:
        return "How rarely the observed state changes relative to elapsed time."

    def calculate(self, data: UserAnalyticsData) -> MetricResult:
        if data.transitions.empty:
            return self.result(self.name, 100.0, {
                "transition_density": 0.0,
                "real_transitions": 0.0,
            })

        transitions = data.transitions.copy()
        real = transitions.loc[
            transitions["old_state"].notna()
            & transitions["new_state"].notna()
            & transitions["old_state"].ne(transitions["new_state"])
        ]
        count = len(real)
        if count == 0:
            return self.result(self.name, 100.0, {
                "transition_density": 0.0,
                "real_transitions": 0.0,
            })

        start, end = self._observation_bounds(data, transitions)
        if pd.isna(start) or pd.isna(end):
            return self.unavailable(self.name, "no real elapsed time available")
        days = max((end - start).total_seconds() / 86400.0, 1.0 / 24.0)
        density = count / days * 30.0
        score = Normalizer.inverse_linear(
            density,
            0.0,
            self._reference_transitions_per_30_days,
        )
        return self.result(self.name, score, {
            "transition_density": Normalizer.clamp(
                density / max(self._reference_transitions_per_30_days, 1e-12) * 100.0
            ),
            "real_transitions": float(count),
        })

    @staticmethod
    def _observation_bounds(
        data: UserAnalyticsData,
        transitions: pd.DataFrame,
    ) -> tuple[pd.Timestamp, pd.Timestamp]:
        if not data.sessions.empty:
            starts = pd.to_datetime(data.sessions["datetime_start"], utc=True, errors="coerce")
            ends = pd.to_datetime(data.sessions["datetime_end"], utc=True, errors="coerce")
            start = starts.min()
            end = ends.max()
            if pd.notna(start) and pd.notna(end) and end >= start:
                return start, end

        timestamps = pd.to_datetime(transitions["timestamp"], utc=True, errors="coerce")
        return timestamps.min(), timestamps.max()
