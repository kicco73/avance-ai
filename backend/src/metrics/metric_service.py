"""MetricService: everything concerning a project's metrics lives here —
both metrics.metrics_framework's core, always-on metrics (engagement,
retention, ...) and its expert-annotation-vs-actual benchmark metrics.
Architecturally analogous to AiService/ChatService/TrackingService
(instantiated once in main.py, constructor-injected everywhere it's
needed — see main.py's own wiring) rather than something another service
builds for itself. A leaf service: depends only on `db` and metrics.
metrics_framework, never on ChatService or TrackingService — TrackingService
(via its own AutoTracker, for trigger-evaluation's merge_if_referenced)
and ChatService (for the Inspector Metrics tab / benchmark Performance
tab / an action's own `env:` field eval scope) both consume this
directly; it never consumes either of them back, so the dependency only
ever points one way.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from automaton.automaton import Automaton
from chat.session_manager import DEFAULT_OPEN_WINDOW_MINUTES
from db import Db
from metrics.metrics_framework import (
    AnalyticsCalculator,
    BenchmarkCalculator,
    BenchmarkConfiguration,
    MetricCalculator,
    MetricResult,
)
from metrics.metrics_framework import metric_names as _metric_names

GetUsername = Callable[[], str]
GetActiveProjectName = Callable[[], str]
GetMaxSessionDurationInMinutes = Callable[[], float]


class MetricService(object):
    def __init__(
        self,
        db: Db,
        get_username: GetUsername,
        get_active_project_name: GetActiveProjectName,
        # Same single source of truth ChatSessionManager's own open-
        # session window uses (config.yml's chat-service.
        # max_session_duration_in_minutes, see main.py) — reused here
        # rather than duplicating the "60.0" default independently, and
        # never taken from a live ChatSessionManager instance itself:
        # this service has no other reason to depend on chat/ at all.
        get_max_session_duration_in_minutes: GetMaxSessionDurationInMinutes = lambda: DEFAULT_OPEN_WINDOW_MINUTES,
    ) -> None:
        self._db = db
        self._get_username = get_username
        self._get_active_project_name = get_active_project_name
        self._get_max_session_duration_in_minutes = get_max_session_duration_in_minutes

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
        the "Label sessions" view's point-in-time Inspector (`until`
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
        `names` dict alongside signal values (see merge_if_referenced),
        and into an action's own `env:` field eval scope (see
        chat_service.py's/tracking/auto_tracker.py's own
        _apply_action_env)."""
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

    def get_benchmark_metrics(self, session_id: int | None = None) -> list[dict]:
        """Expert-annotation-vs-actual benchmark metrics (see metrics.
        metrics_framework.benchmark_metrics) for the active user+project —
        every annotated session, or (session_id given) just that one. Same
        {name, ui_label, ui_description, value} shape as calculate_all,
        plus `sample_count` (how many annotated points fed each metric —
        see the framework's own README on why that must never be
        discarded alongside the score) — the "Label sessions" view's
        Performance tab. Ownership of `session_id`, when given, is the
        caller's own responsibility (see ChatService.get_benchmark_metrics)."""
        configuration = BenchmarkConfiguration(
            max_session_duration_in_minutes=self._get_max_session_duration_in_minutes()
        )
        calculator = BenchmarkCalculator(
            self._db, self._get_username(), self._get_active_project_name(),
            configuration=configuration, session_id=session_id,
        )
        results = calculator.calculate_all()
        return [
            {
                "name": metric.name,
                "ui_label": metric.ui_label,
                "ui_description": metric.ui_description,
                "value": result.value,
                "sample_count": result.sample_count,
            }
            for metric, result in zip(calculator.metrics, results)
        ]
