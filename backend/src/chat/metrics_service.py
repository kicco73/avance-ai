"""Computes the metrics_framework's core metrics for the active user+project,
on demand — no caching (see metrics_framework/README.md #16). Instantiated
as ChatService's `metrics`, same DI style as chat/signals.py's `Signals`."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from automaton.automaton import Automaton
from db import Db
from metrics_framework import AnalyticsCalculator, MetricCalculator, MetricResult
from metrics_framework import metric_names as _metric_names

GetUsername = Callable[[], str]
GetActiveProjectName = Callable[[], str]


class ChatMetrics(object):
    def __init__(self, db: Db, get_username: GetUsername, get_active_project_name: GetActiveProjectName) -> None:
        self._db = db
        self._get_username = get_username
        self._get_active_project_name = get_active_project_name

    def _calculate(self, until: datetime | None = None) -> list[tuple[MetricCalculator, MetricResult]]:
        """One AnalyticsCalculator per call: loads the analytical dataset
        once, then evaluates every core metric against it — the one place
        that construction happens, shared by calculate_all/calculate_values
        so neither duplicates it. `until` restricts the dataset to what
        existed at or before that point (see AnalyticsCalculator)."""
        calculator = AnalyticsCalculator(
            self._db, self._get_username(), self._get_active_project_name(), until=until
        )
        return list(zip(calculator.metrics, calculator.calculate_all()))

    def calculate_all(self, until: datetime | None = None) -> list[dict]:
        """ui_label/ui_description/value per metric, for the "Edit
        project" view's Inspector Metrics tab (live, `until` omitted) and
        the "Benchmark project" view's point-in-time Inspector (`until`
        set to a specific past message's timestamp — see
        ChatService.get_metrics)."""
        return [
            {
                "name": metric.name,
                "ui_label": metric.ui_label,
                "ui_description": metric.ui_description,
                "value": result.value,
            }
            for metric, result in self._calculate(until=until)
        ]

    def calculate_values(self) -> dict[str, float]:
        """Flat {name: value} — for merging into trigger-evaluation's
        `names` dict alongside signal values (see merge_if_referenced)."""
        return {metric.name: result.value for metric, result in self._calculate()}

    def merge_if_referenced(self, automaton: Automaton, state_key: str, names: dict[str, Any]) -> dict[str, Any]:
        """`names` (signal values headed for trigger evaluation — see
        automaton.py's Automaton.evaluate_triggers/preview_triggers),
        augmented with core metric values — but only when at least one
        triggerable action leaving `state_key` actually references a
        metric name (see Automaton.triggers_reference). Computing metrics
        means loading this user+project's whole message/session/signal
        history (see AnalyticsCalculator) — worth skipping whenever a
        project's triggers never mention one at all, which is the common
        case for most turns/projects."""
        if not automaton.triggers_reference(state_key, _metric_names()):
            return names
        return {**names, **self.calculate_values()}
