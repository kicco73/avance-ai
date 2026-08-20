from __future__ import annotations

import json
import math

import numpy as np

from ..dto import MetricResult, UserAnalyticsData
from ..normalization import Normalizer
from ..timeline import Timeline
from .base import BaseMetric


class SignalStabilityMetric(BaseMetric):
    """Aggregated temporal stability of all numeric signals: mean absolute
    successive change per signal, normalized against a configurable
    reference and inverted. Signals with fewer than two observations are ignored."""

    def __init__(self, change_reference: float = 25.0) -> None:
        self._change_reference = change_reference

    @property
    def name(self) -> str:
        return "signal_stability"

    @property
    def ui_label(self) -> str:
        return "Signal stability"

    @property
    def ui_description(self) -> str:
        return "How stable the tracked numeric signals remain over time, averaged across all of them."

    def calculate(self, data: UserAnalyticsData) -> MetricResult:
        names = self._signal_names(data)
        scores: dict[str, float] = {}
        for name in names:
            series = Timeline(data).signal_series(name)
            if len(series) < 2:
                continue
            changes = series.diff().dropna().abs()
            mean_change = float(changes.mean())
            scores[name] = Normalizer.inverse_linear(
                mean_change, 0.0, self._change_reference
            )

        if not scores:
            return self.result(self.name, 0.0)

        value = float(np.mean(list(scores.values())))
        return self.result(self.name, value, scores)

    @staticmethod
    def _signal_names(data: UserAnalyticsData) -> set[str]:
        names: set[str] = set()
        for raw in data.signals.get("values", []):
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    continue
            if isinstance(raw, dict):
                names.update(
                    key for key, value in raw.items()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(float(value))
                )
        return names
